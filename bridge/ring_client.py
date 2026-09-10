"""Direkte Anbindung an die Ring Cloud API (ueber die Bibliothek
``ring_doorbell``) - macht die Bridge unabhaengig von einer separat
laufenden ``ring-mqtt``-Instanz.

Login laeuft einmalig per E-Mail/Passwort (+ 2FA-Code) ueber das
Web-Interface, danach wird ein OAuth-Refresh-Token in der Konfiguration
gespeichert und automatisch erneuert.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import Callable

from ring_doorbell import (
    Auth,
    AuthenticationError,
    Requires2FAError,
    Ring,
    RingEvent,
    RingEventListener,
)

from .config import ConfigStore

log = logging.getLogger("bridge.ring")

__all__ = ["RingClient", "AuthenticationError", "Requires2FAError"]

USER_AGENT = "homekey-ringmqtt-bridge/1.0"


class RingClient:
    def __init__(self, config: ConfigStore):
        self.config = config
        self._auth: Auth | None = None
        self._ring: Ring | None = None
        self._lock = threading.RLock()

        self._listener: RingEventListener | None = None
        self._on_ding: Callable[[str], None] | None = None

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        self._init_from_config()

    # -- asyncio-Bruecke: ring_doorbell ist async, die Bridge ist es nicht --
    def _run(self, coro, timeout: float = 20.0):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _hardware_id(self) -> str:
        cfg = self.config.get()
        hw_id = cfg["ring"].get("hardware_id")
        if not hw_id:
            hw_id = uuid.uuid4().hex
            self.config.update({"ring": {"hardware_id": hw_id}})
        return hw_id

    def _save_token(self, token: dict):
        self.config.update({"ring": {"token": token}})

    def _init_from_config(self):
        cfg = self.config.get()
        token = cfg["ring"].get("token")
        if not token:
            return
        with self._lock:
            self._auth = Auth(USER_AGENT, token, self._save_token, hardware_id=self._hardware_id())
            self._ring = Ring(self._auth)

    # -- Login / 2FA ----------------------------------------------------------
    def login(self, email: str, password: str, otp_code: str | None = None):
        """Fuehrt den (einmaligen) Ring-Login durch.

        Wirft ``Requires2FAError``, wenn noch ein 2FA-Code eingegeben werden
        muss, oder ``AuthenticationError`` bei falschen Zugangsdaten.
        """
        self.stop_ding_listener()
        auth = Auth(USER_AGENT, None, self._save_token, hardware_id=self._hardware_id())
        self._run(auth.async_fetch_token(email, password, otp_code))
        ring = Ring(auth)
        # Session/Geraeteliste einmal laden, um den Login wirklich zu pruefen.
        self._run(ring.async_update_devices())

        with self._lock:
            self._auth = auth
            self._ring = ring
        self.config.update({"ring": {"email": email}})

    def logout(self):
        self.stop_ding_listener()
        with self._lock:
            self._auth = None
            self._ring = None
        self.config.update(
            {
                "ring": {
                    "token": {},
                    "email": "",
                    "device_id": None,
                    "device_name": "",
                    "fcm_credentials": {},
                }
            }
        )

    def is_authenticated(self) -> bool:
        return bool(self.config.get()["ring"].get("token"))

    # -- Geraete ----------------------------------------------------------
    def list_intercoms(self) -> list[dict]:
        with self._lock:
            ring = self._ring
        if ring is None:
            raise RuntimeError("Ring ist nicht angemeldet.")
        self._run(ring.async_update_devices())
        return [{"id": dev.id, "name": dev.name} for dev in ring.devices().other]

    def battery_life(self) -> int | None:
        """Akkustand (0-100) des ausgewaehlten Intercoms, oder None, wenn
        nicht angemeldet/konfiguriert bzw. das Geraet keinen Akku verbaut hat
        (der Ring Intercom ist festverdrahtet, ein Akku ist nur optional)."""
        cfg = self.config.get()
        device_id = cfg["ring"].get("device_id")
        with self._lock:
            ring = self._ring
        if ring is None or not device_id:
            return None
        try:
            self._run(ring.async_update_devices())
            device = ring.devices().get_other(int(device_id))
            return device.battery_life
        except Exception:
            log.exception("Ring-Akkustand konnte nicht abgerufen werden")
            return None

    # -- Ring-Klingel-Ereignisse ("Dings") - dauerhaftes Push-Listening --------
    def _save_fcm_credentials(self, creds: dict):
        self.config.update({"ring": {"fcm_credentials": creds}})

    def _on_ring_event(self, event: RingEvent):
        if event.is_update or event.kind != "ding":
            return
        cfg = self.config.get()
        device_id = cfg["ring"].get("device_id")
        if not device_id or event.doorbot_id != int(device_id):
            return
        if self._on_ding:
            self._on_ding(event.device_name)

    def start_ding_listener(self, on_ding: Callable[[str], None]):
        """Registriert sich einmalig bei Ring fuer Push-Benachrichtigungen
        (Firebase Cloud Messaging) und haelt die Verbindung dauerhaft offen -
        kein Polling. Ein neues Ding kommt so quasi in Echtzeit an."""
        cfg = self.config.get()
        if not (cfg["ring"].get("enabled") and cfg["ring"].get("device_id")):
            return
        with self._lock:
            ring = self._ring
            if ring is None or self._listener is not None:
                return
            self._on_ding = on_ding
            fcm_credentials = cfg["ring"].get("fcm_credentials") or None
            listener = RingEventListener(
                ring, fcm_credentials, self._save_fcm_credentials
            )
            listener.add_notification_callback(self._on_ring_event)
            try:
                started = self._run(listener.start(), timeout=15.0)
            except Exception:
                log.exception("Ring-Klingel-Listener konnte nicht gestartet werden")
                return
            if not started:
                log.warning("Ring-Klingel-Listener konnte sich nicht bei Ring registrieren")
                return
            self._listener = listener
            log.info("Ring-Klingel-Listener gestartet (dauerhaftes Push-Listening)")

    def stop_ding_listener(self):
        with self._lock:
            listener = self._listener
            self._listener = None
            self._on_ding = None
        if listener is None:
            return
        try:
            self._run(listener.stop())
        except Exception:
            log.exception("Ring-Klingel-Listener konnte nicht sauber gestoppt werden")

    # -- Tuer oeffnen -------------------------------------------------------
    def open_door(self) -> bool:
        cfg = self.config.get()
        device_id = cfg["ring"].get("device_id")
        with self._lock:
            ring = self._ring
        if ring is None or not device_id:
            log.warning("Ring-Intercom nicht angemeldet/konfiguriert - kein Oeffnen moeglich.")
            return False
        try:
            self._run(ring.async_update_devices())
            device = ring.devices().get_other(int(device_id))
            ok = self._run(device.async_open_door())
            if ok:
                log.info("Ring-Intercom geoeffnet (%s)", device.name)
            else:
                log.warning("Ring-Intercom hat das Oeffnen abgelehnt (%s)", device.name)
            return bool(ok)
        except Exception:
            log.exception("Ring-Intercom Oeffnen fehlgeschlagen")
            return False
