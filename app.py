"""NFC Intercom Bridge fuer HomeKey-ESP32 + ring-mqtt.

Web-Oberflaeche + MQTT-Bruecke: laesst dich eigene NFC-Tags anlernen und
sorgt dafuer, dass sowohl ein Apple-HomeKey-Tap als auch ein bekannter
NFC-Tag am HomeKey-ESP32-Leser die Ring-Gegensprechanlage (ueber
ring-mqtt) bzw. optional den lokalen Riegel oeffnet.

Start:
    python app.py [--config config.json] [--db bridge.db]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from bridge import events
from bridge.config import ConfigStore
from bridge.mqtt_bridge import Bridge
from bridge.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridge.app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="static", template_folder="templates")

config_store: ConfigStore
store: Store
bridge: Bridge


def create_app(config_path: str, db_path: str) -> Flask:
    global config_store, store, bridge
    config_store = ConfigStore(config_path)
    store = Store(db_path)
    bridge = Bridge(config_store, store)
    return app


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "templates"), "index.html")


# -- status / live events -------------------------------------------------
@app.get("/api/status")
def api_status():
    return jsonify(
        {
            "mqtt_connected": bridge.is_connected(),
            "config": config_store.redacted(),
        }
    )


@app.get("/api/events")
def api_events():
    q = events.subscribe()

    def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            events.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream")


# -- config -----------------------------------------------------------------
@app.get("/api/config")
def api_get_config():
    return jsonify(config_store.redacted())


@app.post("/api/config")
def api_update_config():
    patch = request.get_json(force=True, silent=True) or {}
    # Never let the redacted placeholder overwrite the real password.
    mqtt_patch = patch.get("mqtt") or {}
    if mqtt_patch.get("password") == "********":
        mqtt_patch.pop("password")
    cfg = config_store.update(patch)
    bridge.reload()
    redacted = config_store.redacted()
    return jsonify(redacted)


# -- credentials (NFC tags / HomeKey endpoints) -----------------------------
@app.get("/api/credentials")
def api_list_credentials():
    return jsonify(store.list_credentials())


@app.post("/api/credentials")
def api_add_credential():
    body = request.get_json(force=True, silent=True) or {}
    kind = body.get("kind")
    identifier = (body.get("identifier") or "").strip()
    name = (body.get("name") or "").strip()
    if kind not in ("nfc", "homekey"):
        return jsonify({"error": "kind muss 'nfc' oder 'homekey' sein"}), 400
    if not identifier:
        return jsonify({"error": "identifier fehlt"}), 400
    if not name:
        name = identifier
    try:
        cred = store.add_credential(kind, identifier, name)
    except Exception as exc:  # unique constraint etc.
        return jsonify({"error": str(exc)}), 400
    return jsonify(cred), 201


@app.put("/api/credentials/<int:cred_id>")
def api_update_credential(cred_id: int):
    body = request.get_json(force=True, silent=True) or {}
    store.update_credential(
        cred_id,
        name=body.get("name"),
        enabled=body.get("enabled"),
    )
    return jsonify({"ok": True})


@app.delete("/api/credentials/<int:cred_id>")
def api_delete_credential(cred_id: int):
    store.delete_credential(cred_id)
    return jsonify({"ok": True})


# -- learn mode ---------------------------------------------------------
@app.post("/api/learn/start")
def api_learn_start():
    body = request.get_json(force=True, silent=True) or {}
    kind = body.get("kind")  # "nfc", "homekey" oder None fuer beides
    bridge.start_learn(kind=kind, timeout=float(body.get("timeout", 30)))
    return jsonify({"ok": True})


@app.post("/api/learn/cancel")
def api_learn_cancel():
    bridge.cancel_learn()
    return jsonify({"ok": True})


@app.get("/api/learn/status")
def api_learn_status():
    return jsonify(bridge.learn_status())


# -- ring topic discovery -------------------------------------------------
@app.post("/api/discover/ring/start")
def api_discover_ring_start():
    body = request.get_json(force=True, silent=True) or {}
    bridge.start_ring_discovery(duration=float(body.get("duration", 15)))
    return jsonify({"ok": True})


@app.get("/api/discover/ring/status")
def api_discover_ring_status():
    return jsonify(bridge.ring_discovery_status())


# -- log ---------------------------------------------------------------
@app.get("/api/log")
def api_log():
    limit = int(request.args.get("limit", 100))
    return jsonify(store.recent_log(limit=limit))


# -- manual trigger (Testknopf) -------------------------------------------
@app.post("/api/open")
def api_open():
    action = bridge.manual_open()
    return jsonify({"ok": True, "action": action})


def main():
    parser = argparse.ArgumentParser(description="HomeKey-ESP32 NFC Intercom Bridge")
    parser.add_argument("--config", default=os.path.join(BASE_DIR, "config.json"))
    parser.add_argument("--db", default=os.path.join(BASE_DIR, "bridge.db"))
    parser.add_argument("--host", default=None, help="Ueberschreibt web.host aus der Konfiguration")
    parser.add_argument("--port", type=int, default=None, help="Ueberschreibt web.port aus der Konfiguration")
    args = parser.parse_args()

    create_app(args.config, args.db)
    cfg = config_store.get()
    host = args.host or cfg["web"]["host"]
    port = args.port or cfg["web"]["port"]
    log.info("Starte Web-Interface auf http://%s:%s", host, port)
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
