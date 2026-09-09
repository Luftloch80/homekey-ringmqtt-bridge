# homekey-ringmqtt-bridge

Ein kleines Python-Programm mit Web-Interface, das **HomeKey-ESP32** und
**ring-mqtt** miteinander verbindet:

- Jeder Tap mit einem **Apple HomeKey** (iPhone/Apple Watch) am
  HomeKey-ESP32-Leser oeffnet die Ring-Gegensprechanlage.
- Zusaetzlich kannst du eigene **NFC-Tags** (Karten, Anhaenger, Sticker)
  ueber das Web-Interface "anlernen". Ein Tap mit einem bekannten Tag
  loest ebenfalls das Oeffnen aus.
- Unbekannte Tags/HomeKey-Endpoints werden ignoriert, aber protokolliert.

Das Programm selbst spricht **kein** HomeKit und **keine** Ring-Cloud-API -
es ist rein eine MQTT-Bruecke: Es liest die Tap-Events, die
HomeKey-ESP32 sowieso schon auf MQTT veroeffentlicht, trifft die
Zugriffsentscheidung und schickt bei Erfolg den Unlock-Befehl an das
`lock`-Geraet, das `ring-mqtt` fuer deine Ring Intercom anlegt.

## Voraussetzungen

- Ein laufender MQTT-Broker (z. B. Mosquitto), an den **sowohl**
  HomeKey-ESP32 **als auch** ring-mqtt angeschlossen sind.
- HomeKey-ESP32 mit aktiviertem MQTT (Web-UI &rarr; Settings &rarr; MQTT).
  Wichtig: Die Option *"Do not publish NFC Tag UID"* muss **deaktiviert**
  sein, wenn du auch normale NFC-Tags (nicht nur HomeKey) nutzen willst.
- `ring-mqtt` mit eingerichteter Ring Intercom.
- Python 3.10+

## Installation

```bash
git clone https://github.com/Luftloch80/homekey-ringmqtt-bridge
cd homekey-ringmqtt-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
python app.py
```

Danach ist die Oberflaeche unter `http://<host>:8098` erreichbar.

Alternativ mit Docker:

```bash
git clone https://github.com/Luftloch80/homekey-ringmqtt-bridge
cd homekey-ringmqtt-bridge
docker compose -f docker-compose.example.yml up -d --build
```

Fuer einen dauerhaften Betrieb auf einem Linux-Host ohne Docker liegt eine
Beispiel-Unit unter `nfc-intercom-bridge.service.example` bei.

## Einrichtung (im Web-Interface)

1. **Einstellungen &rarr; MQTT-Broker**: Host/Port/Zugangsdaten deines
   Brokers eintragen.
2. **Einstellungen &rarr; HomeKey-ESP32**: Die Geraete-/Client-ID
   eintragen, die du in der HomeKey-ESP32-MQTT-Konfiguration vergeben
   hast (Standard-Ableitung: `HK-XXXXXX` aus der MAC-Adresse). Die
   benoetigten Topics (`<id>/homekey/auth`, `<id>/homekit/set_target_state`,
   ...) werden daraus automatisch abgeleitet.
3. **Einstellungen &rarr; Ring-Intercom**: Aktivieren und entweder das
   Befehls-Topic manuell eintragen oder auf **"Ring-Topics automatisch
   suchen"** klicken. Die Bridge abonniert dann kurzzeitig `ring/#`, du
   loest in der Ring-App/-Erwartung ein Ereignis aus (z. B. einmal
   klingeln oder in Home Assistant/ring-mqtt manuell entriegeln) und die
   gefundenen Topics werden als Vorschlaege angezeigt. Per Klick uebernimmst
   du das passende `.../lock/command`-Topic.
   Payload zum Oeffnen ist bei ring-mqtt normalerweise `unlock`.
4. **Zugangskarten &rarr; Neuen NFC-Tag anlernen**: Button klicken, Tag an
   den HomeKey-ESP32-Leser halten, Namen vergeben, speichern.
5. Fertig - ab jetzt oeffnet sowohl HomeKey als auch jeder angelernte Tag
   die Intercom.

### HomeKey-Taps einschraenken

Standardmaessig vertraut die Bridge **jedem** gueltigen HomeKey-Tap (das
Geraet hat bereits eine kryptographische Authentifizierung durchlaufen,
bevor HomeKey-ESP32 das Event ueberhaupt veroeffentlicht). Willst du das
einschraenken (z. B. nur bestimmte iPhones sollen die Intercom oeffnen,
nicht nur den lokalen Riegel), deaktiviere *"Jedem gueltigen HomeKey-Tap
vertrauen"* und trage die gewuenschten Endpoint-IDs unter *Zugangskarten
&rarr; HomeKey manuell hinzufuegen* ein (die ID siehst du im Live-Feed,
nachdem einmal getappt wurde).

## Wie die Zugriffsentscheidung funktioniert

```
HomeKey-ESP32 --MQTT--> <id>/homekey/auth --JSON--> Bridge
                                                        |
                                looked up: NFC-UID oder HomeKey-Endpoint-ID
                                                        |
                                    erlaubt? --nein--> nur Log-Eintrag
                                        |
                                       ja
                                        |
                     ring-mqtt lock/command = "unlock"  (+ optional lokaler Riegel)
```

Die Bridge selbst haelt keinen eigenen NFC-Leser oder HomeKit-Server -
sie nutzt konsequent den bereits vorhandenen HomeKey-ESP32-Leser als
einzige Hardware.

## Konfigurationsdatei

`config.json` (siehe `config.example.json`) wird ueber das Web-Interface
verwaltet, kann aber auch direkt editiert werden:

| Feld | Bedeutung |
|---|---|
| `mqtt.*` | Verbindungsdaten zum MQTT-Broker |
| `homekey.device_id` | Geraete-/Client-ID von HomeKey-ESP32 |
| `homekey.auth_topic` | Topic, auf dem Taps veroeffentlicht werden (`<id>/homekey/auth`) |
| `homekey.trust_all_homekey_taps` | `true` = jeder HomeKey-Tap oeffnet, unabhaengig von der Tag-Liste |
| `homekey.also_trigger_local_lock` | zusaetzlich `<id>/homekit/set_target_state` = `0` (UNLOCKED) publizieren |
| `ring.command_topic` | ring-mqtt Lock-Command-Topic der Intercom |
| `ring.unlock_payload` | Payload zum Oeffnen (Standard `unlock`) |
| `access.cooldown_seconds` | Mindestabstand zwischen zwei Aktionen desselben Tags/HomeKey |

## Sicherheitshinweise

- Das Web-Interface hat **keine eigene Authentifizierung** - es ist fuer
  den Betrieb im vertrauenswuerdigen Heimnetz gedacht. Nicht ungeschuetzt
  ins Internet exponieren (z. B. per Reverse-Proxy mit Login absichern,
  falls Fernzugriff gewuenscht ist).
- Angelernte NFC-UIDs sind (wie bei den meisten guenstigen NFC-Tags) nicht
  kryptographisch gegen Kopieren geschuetzt - anders als HomeKey. Nutze sie
  entsprechend nur dort, wo dieses Risiko akzeptabel ist.
- Die SQLite-Datenbank (`bridge.db`) enthaelt die Liste der Zugangskarten
  im Klartext (UID/Endpoint-ID + Name). Zugriff auf das Dateisystem des
  Hosts entsprechend absichern.
