"""Configuration loading/saving for the NFC Intercom Bridge.

Configuration lives in a single JSON file so it can be edited by hand or
through the web UI. A fresh default configuration is created on first run.
"""
from __future__ import annotations

import copy
import json
import os
import threading

DEFAULT_CONFIG = {
    "mqtt": {
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "client_id": "nfc-intercom-bridge",
        "tls": False,
        "tls_insecure": False,
    },
    "homekey": {
        # Device/Client-ID wie in der HomeKey-ESP32 MQTT-Konfiguration
        "device_id": "",
        # Volle Topics - werden beim Speichern automatisch aus device_id
        # abgeleitet, koennen aber manuell ueberschrieben werden falls in
        # der Firmware andere Topic-Suffixe konfiguriert wurden.
        "auth_topic": "",
        "lock_state_topic": "",
        "availability_topic": "",
        # Jeder gueltige HomeKey-Tap (vom iPhone/Apple Watch) oeffnet die
        # Tuer, ohne dass die Endpoint-ID in der Tag-Liste stehen muss -
        # HomeKey hat bereits eine kryptographische Authentifizierung
        # durchgefuehrt.
        "trust_all_homekey_taps": True,
    },
    "ring": {
        "enabled": False,
        # Per Web-Interface gesetzt (E-Mail/Passwort + 2FA) - Bridge spricht
        # direkt mit der Ring Cloud API, kein ring-mqtt noetig.
        "email": "",
        "hardware_id": "",
        "token": {},
        "device_id": None,
        "device_name": "",
    },
    "access": {
        "cooldown_seconds": 3,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8098,
    },
}

_lock = threading.Lock()


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        # Ein leeres dict als Override kann nur "auf leer zuruecksetzen"
        # bedeuten - ein rekursiver Merge waere hier ein No-Op und wuerde
        # den bestehenden Wert (z.B. beim Ring-Logout den Token) nie loeschen.
        if isinstance(value, dict) and value and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigStore:
    def __init__(self, path: str):
        self.path = path
        self._config = None
        self.load()

    def load(self) -> dict:
        with _lock:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                self._config = _deep_merge(DEFAULT_CONFIG, on_disk)
            else:
                self._config = copy.deepcopy(DEFAULT_CONFIG)
            self._derive_topics_locked()
            self._save_locked()
            return copy.deepcopy(self._config)

    def get(self) -> dict:
        with _lock:
            return copy.deepcopy(self._config)

    def update(self, patch: dict) -> dict:
        with _lock:
            self._config = _deep_merge(self._config, patch)
            self._derive_topics_locked()
            self._save_locked()
            return copy.deepcopy(self._config)

    def _derive_topics_locked(self):
        hk = self._config["homekey"]
        device_id = (hk.get("device_id") or "").strip()
        if not device_id:
            return
        if not hk.get("auth_topic"):
            hk["auth_topic"] = f"{device_id}/homekey/auth"
        if not hk.get("lock_state_topic"):
            hk["lock_state_topic"] = f"{device_id}/homekit/state"
        if not hk.get("availability_topic"):
            hk["availability_topic"] = f"{device_id}/status"

    def _save_locked(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self._config, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, self.path)

    def redacted(self) -> dict:
        cfg = self.get()
        if cfg["mqtt"].get("password"):
            cfg["mqtt"]["password"] = "********"
        # Als String statt {}/dict senden, damit das Web-UI simpel auf
        # Wahrheitswert pruefen kann (ein leeres dict ist in JS truthy).
        cfg["ring"]["token"] = "********" if cfg["ring"].get("token") else ""
        return cfg
