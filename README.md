# homekey-ringmqtt-bridge

Ein kleines Python-Programm mit Web-Interface, das **HomeKey-ESP32** direkt
mit deiner **Ring-Gegensprechanlage** verbindet - als eigenstaendige
Loesung, ohne dass zusaetzlich `ring-mqtt` laufen muss:

- Jeder Tap mit einem **Apple HomeKey** (iPhone/Apple Watch) am
  HomeKey-ESP32-Leser oeffnet die Ring-Gegensprechanlage.
- Zusaetzlich kannst du eigene **NFC-Tags** (Karten, Anhaenger, Sticker)
  ueber das Web-Interface "anlernen". Ein Tap mit einem bekannten Tag
  loest ebenfalls das Oeffnen aus.
- Unbekannte Tags/HomeKey-Endpoints werden ignoriert, aber protokolliert.

Das Programm spricht **kein** HomeKit, benoetigt fuer HomeKey-ESP32 aber
weiterhin **MQTT**: Es liest die Tap-Events, die HomeKey-ESP32 sowieso
schon auf MQTT veroeffentlicht, trifft die Zugriffsentscheidung und
oeffnet bei Erfolg die Ring Intercom **direkt ueber die Ring Cloud API**
(Login einmalig im Web-Interface) - optional zusaetzlich den lokalen
HomeKey-ESP32-Riegel/Relais.

## Voraussetzungen

- Ein laufender MQTT-Broker (z. B. Mosquitto), an den HomeKey-ESP32
  angeschlossen ist.
- HomeKey-ESP32 mit aktiviertem MQTT (Web-UI &rarr; Settings &rarr; MQTT).
  Wichtig: Die Option *"Do not publish NFC Tag UID"* muss **deaktiviert**
  sein, wenn du auch normale NFC-Tags (nicht nur HomeKey) nutzen willst.
- Ein Ring-Account mit eingerichteter Ring Intercom.
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
3. **Einstellungen &rarr; Ring-Intercom**: "Ring-Intercom-Steuerung
   aktivieren" ankreuzen und speichern.
4. **Einstellungen &rarr; Ring-Konto**: E-Mail und Passwort deines
   Ring-Accounts eingeben und auf "Anmelden" klicken. Fordert Ring einen
   2FA-Code an (per E-Mail/SMS), erscheint ein zusaetzliches Feld - Code
   eingeben und erneut auf "Anmelden" klicken. Danach auf "Geraete laden"
   klicken, die gewuenschte Intercom aus der Liste auswaehlen und
   "Uebernehmen" klicken. Der Login laeuft einmalig; der Zugriffstoken
   wird danach automatisch erneuert.
5. **Zugangskarten &rarr; Neuen NFC-Tag anlernen**: Button klicken, Tag an
   den HomeKey-ESP32-Leser halten, Namen vergeben, speichern.
6. Fertig - ab jetzt oeffnet sowohl HomeKey als auch jeder angelernte Tag
   die Intercom.

### HomeKey-Taps einschraenken

Standardmaessig vertraut die Bridge **jedem** gueltigen HomeKey-Tap (das
Geraet hat bereits eine kryptographische Authentifizierung durchlaufen,
bevor HomeKey-ESP32 das Event ueberhaupt veroeffentlicht). Willst du das
einschraenken (z. B. nur bestimmte iPhones sollen die Intercom oeffnen,
nicht nur den lokalen Riegel), deaktiviere *"Jedem gueltigen HomeKey-Tap
vertrauen"* in den Einstellungen und trage die gewuenschten Endpoint-IDs
per API ein (die ID siehst du im Live-Feed, nachdem einmal getappt
wurde) - eine UI dafuer gibt es aktuell nicht mehr:

```bash
curl -X POST http://<host>:8098/api/credentials \
  -H "Content-Type: application/json" \
  -d '{"kind": "homekey", "identifier": "<Endpoint-ID>", "name": "iPhone Anna"}'
```

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
                Ring Cloud API: Intercom "open door"  (+ optional lokaler Riegel)
```

Die Bridge selbst haelt keinen eigenen NFC-Leser oder HomeKit-Server -
sie nutzt konsequent den bereits vorhandenen HomeKey-ESP32-Leser als
einzige Hardware. Fuer die Ring-Steuerung ist ausser dem einmaligen
Konto-Login im Web-Interface keine weitere Software noetig.

### Manuelles Entsperren in der Home-App

Wird der Riegel unabhaengig von einem Tap direkt in der Apple-Home-App
entsperrt (Schieberegler antippen), meldet HomeKey-ESP32 den neuen
Zustand ueber `<id>/homekit/state` per MQTT. Die Bridge abonniert dieses
Topic ebenfalls und oeffnet in diesem Fall zusaetzlich die Ring-Intercom -
vorausgesetzt, Ring ist aktiviert und ein Geraet ausgewaehlt. Damit das
nicht mit dem eigenen `also_trigger_local_lock`-Befehl kollidiert (der
denselben Zustandswechsel ausloest und als Echo auf `homekit/state`
zurueckkommt), ignoriert die Bridge Meldungen, die innerhalb von 10
Sekunden nach einem eigenen Entsperr-Befehl eintreffen. Genau wie bei
Taps gilt zusaetzlich die konfigurierte Cooldown-Zeit.

Der HomeKey-ESP32-Riegel ist als Tueroeffner-Impuls gedacht, kein
Dauerzustand: Egal ob die Bridge selbst `also_trigger_local_lock`
ausloest (per Tap oder ueber "Tuer jetzt oeffnen") oder der Riegel wie
oben beschrieben manuell in der Home-App entsperrt wurde - die Bridge
setzt den HomeKit-Riegel automatisch nach 3 Sekunden wieder auf
"verriegelt" zurueck, damit die Home-App nicht dauerhaft "entsperrt"
anzeigt.

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
| `homekey.lock_state_topic` | wird abonniert (`<id>/homekit/state`) - manuelles Entsperren in der Home-App oeffnet zusaetzlich die Ring-Intercom |
| `ring.enabled` | Ring-Intercom-Steuerung aktiv |
| `ring.email` / `ring.token` | vom Web-Interface beim Login gesetzt (Refresh-Token, kein Passwort) |
| `ring.device_id` / `ring.device_name` | ausgewaehlte Ring-Intercom (Web-Interface &rarr; Ring-Konto) |
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
- `config.json` enthaelt nach dem Ring-Login einen OAuth-Refresh-Token im
  Klartext (kein Passwort, aber ausreichend fuer Zugriff auf dein
  Ring-Konto). Genauso wie bei der SQLite-Datenbank: Dateisystemzugriff
  entsprechend absichern. Ueber "Abmelden" im Web-Interface wird der
  Token wieder geloescht.
