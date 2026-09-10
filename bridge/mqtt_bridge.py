"""MQTT glue: subscribes to HomeKey-ESP32 tap events, applies the access
control decision (allow-listed NFC tag or trusted HomeKey tap) and, on
success, opens the Ring Intercom directly via the Ring Cloud API (see
``ring_client.py``).
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from . import events
from .config import ConfigStore
from .ring_client import RingClient
from .store import Store

log = logging.getLogger("bridge.mqtt")

# Wie oft die Bridge bei Ring nach aktiven Klingel-Ereignissen ("Dings")
# fragt. Ring haelt ein Ding einige Minuten "aktiv", daher reicht ein
# kurzes Polling-Intervall fuer eine gefuehlt sofortige Anzeige.
DING_POLL_INTERVAL = 5.0


class Bridge:
    def __init__(self, config: ConfigStore, store: Store, ring_client: RingClient):
        self.config = config
        self.store = store
        self.ring_client = ring_client
        self.client: mqtt.Client | None = None
        self._connected = False
        self._last_grant: dict[str, float] = {}
        self._seen_dings: set[int] = set()
        self._lock = threading.RLock()

        self._connect_client()
        self._start_ding_poller()

    # -- connection handling ------------------------------------------------
    def _connect_client(self):
        cfg = self.config.get()
        mqtt_cfg = cfg["mqtt"]
        if not mqtt_cfg.get("host"):
            log.warning("MQTT-Broker ist nicht konfiguriert - Bridge startet nicht.")
            return

        client_id = mqtt_cfg.get("client_id") or "nfc-intercom-bridge"
        client = mqtt.Client(client_id=client_id, clean_session=True)

        if mqtt_cfg.get("username"):
            client.username_pw_set(mqtt_cfg.get("username"), mqtt_cfg.get("password") or None)

        if mqtt_cfg.get("tls"):
            client.tls_set(cert_reqs=ssl.CERT_NONE if mqtt_cfg.get("tls_insecure") else ssl.CERT_REQUIRED)
            if mqtt_cfg.get("tls_insecure"):
                client.tls_insecure_set(True)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        self.client = client
        try:
            client.connect_async(mqtt_cfg["host"], int(mqtt_cfg.get("port") or 1883), keepalive=30)
            client.loop_start()
        except Exception:
            log.exception("Verbindung zum MQTT-Broker fehlgeschlagen")

    def reload(self):
        """Reconnect using the current configuration (called after settings change)."""
        with self._lock:
            if self.client:
                try:
                    self.client.loop_stop()
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            self._connected = False
            self._connect_client()

    def is_connected(self) -> bool:
        return self._connected

    # -- paho callbacks -------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            log.error("MQTT connect fehlgeschlagen, rc=%s", rc)
            self._connected = False
            events.publish("mqtt_status", {"connected": False, "error": str(rc)})
            return
        self._connected = True
        log.info("Mit MQTT-Broker verbunden")
        events.publish("mqtt_status", {"connected": True})

        cfg = self.config.get()
        auth_topic = cfg["homekey"].get("auth_topic")
        if auth_topic:
            client.subscribe(auth_topic, qos=0)
            log.info("Abonniert: %s", auth_topic)
        else:
            log.warning("Kein HomeKey-ESP32 auth_topic konfiguriert.")

        lock_state_topic = cfg["homekey"].get("lock_state_topic")
        if lock_state_topic:
            client.subscribe(lock_state_topic, qos=0)
            log.info("Abonniert: %s", lock_state_topic)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        events.publish("mqtt_status", {"connected": False})
        log.warning("MQTT Verbindung getrennt (rc=%s)", rc)

    def _on_message(self, client, userdata, msg):
        try:
            cfg = self.config.get()
            if msg.topic == cfg["homekey"].get("auth_topic"):
                self._handle_auth_message(msg.payload)
            elif msg.topic == cfg["homekey"].get("lock_state_topic"):
                self._handle_lock_state_message(msg.payload)
        except Exception:
            log.exception("Fehler bei der Verarbeitung einer MQTT-Nachricht")

    # -- HomeKey-ESP32 tap handling --------------------------------------
    def _handle_auth_message(self, raw_payload: bytes):
        try:
            payload = json.loads(raw_payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            log.warning("Ungueltiges JSON auf dem HomeKey auth-Topic: %r", raw_payload)
            return

        is_homekey = bool(payload.get("homekey"))
        if is_homekey:
            kind = "homekey"
            identifier = str(payload.get("endpointId", "")).upper()
            extra = {
                "issuerId": payload.get("issuerId"),
                "readerId": payload.get("readerId"),
            }
        else:
            kind = "nfc"
            identifier = str(payload.get("uid", "")).upper()
            extra = {
                "atqa": payload.get("atqa"),
                "sak": payload.get("sak"),
            }

        if not identifier:
            log.warning("Tap-Ereignis ohne Identifier empfangen: %s", payload)
            return

        events.publish("tap", {"kind": kind, "identifier": identifier, **extra})

        # Learn mode: capture the identifier instead of evaluating access.
        if self._maybe_capture_learn(kind, identifier):
            return

        self._evaluate_access(kind, identifier)

    def _evaluate_access(self, kind: str, identifier: str):
        cfg = self.config.get()

        # Debounce repeated taps of the same credential.
        cooldown = float(cfg["access"].get("cooldown_seconds", 3) or 0)
        key = f"{kind}:{identifier}"
        now = time.time()
        with self._lock:
            last = self._last_grant.get(key, 0)
            if now - last < cooldown:
                log.info("Tap %s ignoriert (Cooldown aktiv)", key)
                return

        granted = False
        reason = ""
        name = ""

        if kind == "homekey" and cfg["homekey"].get("trust_all_homekey_taps", True):
            granted = True
            reason = "HomeKey (vertrauenswürdig)"
            cred = self.store.find_credential("homekey", identifier)
            if cred:
                name = cred["name"]
                self.store.mark_used(cred["id"])
        else:
            cred = self.store.find_credential(kind, identifier)
            if cred and cred["enabled"]:
                granted = True
                name = cred["name"]
                reason = "Bekanntes Tag/HomeKey"
                self.store.mark_used(cred["id"])
            elif cred and not cred["enabled"]:
                reason = "Tag ist deaktiviert"
            else:
                reason = "Unbekannter Tag/HomeKey"

        action = ""
        if granted:
            action = self._trigger_open()

        with self._lock:
            if granted:
                self._last_grant[key] = now

        self.store.add_log(kind, identifier, name, granted, reason, action)
        events.publish(
            "access",
            {
                "kind": kind,
                "identifier": identifier,
                "name": name,
                "granted": granted,
                "reason": reason,
                "action": action,
            },
        )

    # -- manuelles Entsperren ueber die Home-App -----------------------------
    def _handle_lock_state_message(self, raw_payload: bytes):
        """HomeKey-ESP32 meldet hier den aktuellen HomeKit-Lock-Zustand -
        auch wenn er nicht ueber die Bridge, sondern direkt in der Home-App
        (manuell entsperren) ausgeloest wurde. Jede "entsperrt"-Meldung
        oeffnet daher zusaetzlich die Ring-Intercom.
        """
        value = raw_payload.decode("utf-8", errors="replace").strip()
        # HomeKit LockCurrentState: 0 == entsperrt (unsecured)
        if value != "0":
            return

        cfg = self.config.get()
        if not (cfg["ring"].get("enabled") and cfg["ring"].get("device_id")):
            return

        cooldown = float(cfg["access"].get("cooldown_seconds", 3) or 0)
        key = "homekit-app"
        now = time.time()
        with self._lock:
            last = self._last_grant.get(key, 0)
            if now - last < cooldown:
                return
            self._last_grant[key] = now

        ok = self.ring_client.open_door()
        action = "ring-intercom" if ok else "ring-intercom(fehlgeschlagen)"
        log.info("Manuell in der Home-App entsperrt - Ring-Intercom %s", "geoeffnet" if ok else "Oeffnen fehlgeschlagen")
        self.store.add_log("homekit", "-", "HomeKit (Home-App)", ok, "Manuell in der Home-App entsperrt", action)
        events.publish(
            "access",
            {
                "kind": "homekit",
                "identifier": "-",
                "name": "HomeKit (Home-App)",
                "granted": ok,
                "reason": "Manuell in der Home-App entsperrt",
                "action": action,
            },
        )

    # -- Ring-Klingel-Ereignisse ("Dings") -----------------------------------
    def _start_ding_poller(self):
        def _poll_loop():
            while True:
                time.sleep(DING_POLL_INTERVAL)
                try:
                    self._poll_dings()
                except Exception:
                    log.exception("Fehler beim Abfragen der Ring-Klingel-Ereignisse")

        threading.Thread(target=_poll_loop, daemon=True).start()

    def _poll_dings(self):
        cfg = self.config.get()
        if not (cfg["ring"].get("enabled") and cfg["ring"].get("device_id")):
            return
        for ding in self.ring_client.list_active_dings():
            with self._lock:
                if ding["id"] in self._seen_dings:
                    continue
                self._seen_dings.add(ding["id"])
                # Verhindert unbegrenztes Wachstum - Ding-IDs sind fortlaufend,
                # die aeltesten fallen zuerst wieder raus.
                if len(self._seen_dings) > 200:
                    self._seen_dings = set(sorted(self._seen_dings)[-100:])
            self._announce_ding()

    def _announce_ding(self):
        log.info("Ring-Intercom: Klingeln erkannt")
        events.publish("ding", {"name": "Ring-Intercom"})

    def _trigger_open(self) -> str:
        cfg = self.config.get()
        if cfg["ring"].get("enabled") and cfg["ring"].get("device_id"):
            if self.ring_client.open_door():
                return "ring-intercom"
            return "ring-intercom(fehlgeschlagen)"
        log.warning("Zugriff gewaehrt, aber Ring-Intercom nicht konfiguriert.")
        return ""

    def manual_open(self) -> str:
        action = self._trigger_open()
        self.store.add_log("manual", "-", "Manuell (Web-UI)", True, "Manueller Test", action)
        events.publish(
            "access",
            {
                "kind": "manual",
                "identifier": "-",
                "name": "Manuell (Web-UI)",
                "granted": True,
                "reason": "Manueller Test",
                "action": action,
            },
        )
        return action

    # -- learn mode -------------------------------------------------------
    def _maybe_capture_learn(self, kind: str, identifier: str) -> bool:
        with self._lock:
            armed = getattr(self, "_learn_armed", False)
            learn_kind = getattr(self, "_learn_kind", None)
        if not armed:
            return False
        if learn_kind and learn_kind != kind:
            return False
        with self._lock:
            self._learn_result = {"kind": kind, "identifier": identifier, "ts": time.time()}
            self._learn_armed = False
        events.publish("learn_captured", self._learn_result)
        return True

    def start_learn(self, kind: str | None = None, timeout: float = 30.0):
        with self._lock:
            self._learn_armed = True
            self._learn_kind = kind
            self._learn_result = None
            self._learn_deadline = time.time() + timeout

    def cancel_learn(self):
        with self._lock:
            self._learn_armed = False
            self._learn_result = None

    def learn_status(self) -> dict:
        with self._lock:
            armed = getattr(self, "_learn_armed", False)
            result = getattr(self, "_learn_result", None)
            deadline = getattr(self, "_learn_deadline", 0)
            if armed and time.time() > deadline:
                self._learn_armed = False
                armed = False
            return {"armed": armed, "result": result}
