# Hausaufgaben → WhatsApp

Holt einmal täglich die neu eingetragenen Hausaufgaben aus Schulmanager Online
und postet sie in eine WhatsApp-Gruppe. An Tagen ohne neue Einträge passiert
nichts.

## Einrichten

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und `libmagic` (braucht
neonize zum Laden; auf dem Mac `brew install libmagic`, auf Debian
`apt install libmagic1`).

```bash
cd ~/hausaufgaben
uv sync
```

Das legt `.venv/` an und installiert das Paket samt `hausaufgaben`-Befehl.
Alle Aufrufe unten laufen über `uv run`.

Zugangsdaten setzen (fürs erste in der Shell, dauerhaft siehe unten):

```bash
export SM_USER="deine@mail.de"
export SM_PASSWORD="…"
```

Alternativ in eine `.env` schreiben (steht in `.gitignore`) und mit
`uv run --env-file .env hausaufgaben …` aufrufen.

## 1. Rohdaten ansehen

Die Schulmanager-API ist inoffiziell, die Feldnamen sind nicht garantiert.
Einmal nachsehen, ob Fach, Text und Termin richtig ankommen:

```bash
uv run hausaufgaben --dump
```

Falls im späteren Text „(kein Text hinterlegt)" oder „?" als Fach auftaucht:
in `src/hausaufgaben/cli.py` die Kandidatenlisten in `hw_text()`, `hw_subject()`
und `hw_due()` um den Feldnamen aus dem Dump ergänzen.

## 2. WhatsApp koppeln

```bash
uv run hausaufgaben --list-groups
```

Zeigt einen QR-Code im Terminal und öffnet ihn zusätzlich als Bild
(`~/.hausaufgaben-wa.qr.png`), weil die Terminal-Darstellung je nach Schrift
oft nicht scannbar ist. In WhatsApp auf dem Handy:
**Einstellungen → Verknüpfte Geräte → Gerät hinzufügen**.

Klappt das Scannen nicht, geht es auch mit einem Kopplungscode:

```bash
uv run hausaufgaben --list-groups --pair-phone +49170xxxxxxx
```

Der 8-stellige Code wird in WhatsApp unter **Gerät hinzufügen → Stattdessen
mit Telefonnummer verknüpfen** eingetippt.

Achtung: WhatsApp erlaubt vier verknüpfte Geräte. Dein Mac-Client belegt eins,
dieses Skript ein zweites.

Für den Scan bleiben fünf Minuten (`WA_PAIR_TIMEOUT`), danach bricht der
Befehl mit Fehler ab. Beim Senden wartet das Skript eine Minute auf die
Verbindung (`WA_TIMEOUT`).

Nach dem Koppeln werden alle Gruppen mit ihrer JID aufgelistet. Die richtige
heraussuchen:

```bash
export WA_GROUP_JID="1203630xxxxxxxxxx@g.us"
```

## 3. Trockenlauf

```bash
uv run hausaufgaben --dry-run
```

Baut die Nachricht und gibt sie aus, ohne zu senden und ohne den State zu
verändern. Beim ersten echten Lauf gilt *alles* als neu — entweder einmal
akzeptieren, oder vorher einmal `--dry-run` weglassen und die erste Nachricht
bewusst absetzen.

## 4. Täglich um 13:00

```bash
cp de.privat.hausaufgaben.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/de.privat.hausaufgaben.plist
```

Die Pfade im Plist zeigen auf dieses Verzeichnis; bei einem Umzug anpassen.

Prüfen: `launchctl list | grep hausaufgaben`, Log unter `run.log`.

`StartCalendarInterval` holt den Lauf nach, wenn der Mac um 13:00 geschlafen
hat — aber nur, wenn er danach wieder aufwacht. Wenn es zuverlässig laufen
soll, ist ein immer erreichbarer Rechner (Raspberry Pi, dein Debian-Server)
der bessere Ort; der Code läuft dort unverändert, nur das launchd-Plist wird
zu einem systemd-Timer.

## Zugangsdaten

Alles steht in `.env` neben `pyproject.toml` (in `.gitignore`):

```
SM_USER=deine@mail.de
SM_PASSWORD=…
WA_GROUP_JID=1203630xxxxxxxxxx@g.us
```

`run.sh` liest sie über `uv run --env-file .env` ein und setzt den PATH, den
launchd sonst nicht mitbringt. Zum Ausprobieren: `./run.sh --dry-run`.

Wer die Zugangsdaten lieber in der macOS-Keychain hätte, kann `run.sh` so
ändern, dass es `SM_PASSWORD` per `security find-generic-password` exportiert
statt die `.env` zu laden.

## Dateien

| Datei | Zweck |
|---|---|
| `pyproject.toml` | Paketdefinition, Abhängigkeiten, `hausaufgaben`-Befehl |
| `src/hausaufgaben/schulmanager.py` | API-Client: Login (PBKDF2-SHA512 → JWT) und `/api/calls` |
| `src/hausaufgaben/wa.py` | WhatsApp-Versand über neonize, plus Gruppenliste |
| `src/hausaufgaben/cli.py` | Der Job: abrufen, gegen State abgleichen, formatieren, senden |
| `run.sh` | Wrapper für launchd: lädt `.env`, ruft `uv run hausaufgaben` |
| `de.privat.hausaufgaben.plist` | launchd-Job für den täglichen Lauf um 13:00 |
| `~/.hausaufgaben-state.json` | Bereits gemeldete Einträge |
| `~/.hausaufgaben-wa.sqlite3` | WhatsApp-Session (wie ein Login — nicht teilen) |

## Vorbehalte

Beide Schnittstellen sind inoffiziell. Die Schulmanager-Endpunkte können sich
mit jedem Deployment ändern; wenn `/api/calls` plötzlich 4xx liefert, ist
meist die `BUNDLE_VERSION` in `src/hausaufgaben/schulmanager.py` veraltet — den aktuellen Wert
findest du in den DevTools des Browsers in einem `/api/calls`-Request.

Automatisierte Clients verstoßen gegen WhatsApps Nutzungsbedingungen. Bei
einer Nachricht pro Tag in eine Vierergruppe ist das Risiko gering, aber nicht
null.
