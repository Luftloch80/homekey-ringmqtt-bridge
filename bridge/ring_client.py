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

from ring_doorbell import Auth, AuthenticationError, Requires2FAError, Ring

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
        with self._lock:
            self._auth = None
            self._ring = None
        self.config.update(
            {"ring": {"token": {}, "email": "", "device_id": None, "device_name": ""}}
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
        return [{"id": dev.id, "name": dev.name} for dev in ring.devices().intercoms]

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
