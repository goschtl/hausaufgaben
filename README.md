# Hausaufgaben → WhatsApp

Holt einmal täglich die neu eingetragenen Hausaufgaben aus Schulmanager Online
und postet sie in eine WhatsApp-Gruppe. An Werktagen (Mo–Fr) ohne neue Einträge
kommt eine kurze „nichts Neues“-Nachricht, am Wochenende passiert dann nichts.
Bei jeder Aufgabe steht, wann das Fach laut Stundenplan das nächste Mal dran
ist. Gibt es für morgen Vertretungen oder Ausfälle, hängen sie unten an (dann
kommt auch sonntags eine Nachricht).

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
und `hw_date()` um den Feldnamen aus dem Dump ergänzen.

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

## 4. Täglich um 13:30

```bash
cp de.privat.hausaufgaben.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/de.privat.hausaufgaben.plist
```

Die Pfade im Plist zeigen auf dieses Verzeichnis; bei einem Umzug anpassen.

Prüfen: `launchctl list | grep hausaufgaben`, Log unter `run.log`.

`StartCalendarInterval` holt den Lauf nach, wenn der Mac um 13:30 geschlafen
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
| `src/hausaufgaben/sprechen.py` | Lokaler Webserver zum Vokabeln-Sprechen mit Aufnahme (`sprechen.html`) |
| `src/hausaufgaben/aussprache.py` | Aussprache-Bewertung per OpenAI-Audiomodell (pydantic-ai) |
| `src/hausaufgaben/vokabeln.py` | Erkennt Vokabel-Aufgaben mit Seitenangabe, liest Wortlisten aus `vokabeln/` |
| `src/hausaufgaben/phase6.py` | Login bei phase6, Units/Karten lesen, Karten freischalten |
| `run.sh` | Wrapper für launchd: lädt `.env`, ruft `uv run hausaufgaben` |
| `de.privat.hausaufgaben.plist` | launchd-Job für den täglichen Lauf um 13:30 |
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

## Vokabeln anhängen

Steht in einer Aufgabe etwas wie „vocabulary p. 147/148“ oder „Vokabeln S. 20–22“,
hängt der Bot die Wörter dieser Seiten an – sofern sie lokal hinterlegt sind:

    vokabeln/buecher.json            {"Ferdinand": {"Englisch": {"ordner": "go-ahead-9",
                                                                 "phase6": "<subject-id>"}}}
    vokabeln/go-ahead-9/147.txt      eine Vokabel pro Zeile, z. B. „ship – Schiff“
    vokabeln/go-ahead-9/seiten.json  {"147": ["Unit 1 | pp. 8/9", "Unit 1 | p. 10"]}

Der Schlüssel in `buecher.json` ist der Vorname aus dem Schulmanager, darunter
das Fach so, wie es in der Aufgabe steht. Fehlende Seiten stehen als
„Vokabeln fehlen: …“ in `run.log`. Der Ordner `vokabeln/` ist in `.gitignore`,
weil die Wortlisten aus den Schulbüchern stammen.

### phase6-Freischaltung

Steht `phase6` im Buch-Eintrag und gibt es für die Seite eine Zuordnung in
`seiten.json`, schaltet der Bot die passenden Units im phase6-Account des Kindes
frei: nur die Hauptwörter aus der Wortliste (Nebenwörter und Beispielsätze,
die phase6 als eigene Karten führt, bleiben aus), nur Deutsch→Englisch, nur noch
nicht aktive Karten – Lernfortschritt bleibt.
Die Nachricht bekommt dann „✅ In phase6 freigeschaltet (N neue Karten)“.

Zugang je Kind in der `.env`:

    export P6_USER_FERDINAND=…
    export P6_PASSWORD_FERDINAND=…

`seiten.json` übersetzt Wortlisten-Seiten hinten im Buch in phase6-Units, deren
Namen die Seiten der Unit-Texte tragen (am Rand der Wortliste abzulesen).
Wörter ändern: Zeile in der Seiten-Datei löschen oder ergänzen, dann

    ./run.sh --phase6-sync Ferdinand 147 148

Das schaltet fehlende Wörter frei und deaktiviert gestrichene – nur in den
Units dieser Seiten. Der tägliche Lauf schaltet dagegen nur frei.

Die Schnittstelle ist inoffiziell; Fehler landen nur in `run.log`, der
Hausaufgaben-Versand läuft trotzdem. `--dry-run` verändert nichts in phase6.

## Vokabeln sprechen üben

    uv run vokabeln-sprechen

öffnet `http://localhost:8765` im Browser. Das Kind sieht das deutsche Wort, tippt aufs
Mikrofon und sagt das englische Wort; die Spracherkennung des Browsers (Chrome oder
Safari, dort muss Siri/Diktat an sein) prüft, ob es passt. Jeder Versuch wird als
Aufnahme in `aufnahmen/<datum>/` gespeichert (mit `.json` daneben: Wort, was erkannt
wurde, richtig/falsch) und lässt sich unter „Aufnahmen anhören“ abspielen.
Wortlisten kommen aus `vokabeln/` (alle Kinder mit Englischbuch in `buecher.json`).
Der Server hört nur auf localhost. Chrome schickt die Sprache zur Erkennung an Google.

**KI-Aussprache (optional):** Steht `export OPENAI_API_KEY=…` in der `.env`, schickt
der Server jede Aufnahme (per ffmpeg nach WAV gewandelt) über pydantic-ai an ein
OpenAI-Audiomodell (Standard `gpt-audio-1.5`, änderbar mit `HA_AUSSPRACHE_MODELL`; Ergebnis per Tool-Call, weil Audiomodelle keinen JSON-Modus können).
Das Kind bekommt 1–3 Sterne und einen kurzen Tipp zur Aussprache; beides steht auch in
der `.json` zur Aufnahme und unter „Aufnahmen anhören“. Die Aufnahmen gehen dafür an OpenAI.

### Rechte Spalte (Gegenteile, Wortfamilien, Wendungen)

Was rechts neben den Vokabeln steht, kommt in `vokabeln/<buch>/<seite>.rechts.txt`
(Format oben in `vokabeln.py`). Spiel und Sprech-Training machen daraus eigene Fragen,
z. B. „Gegenteil von beginning“ → *end* oder „Nomen zu (to) apologize“ → *apology*.
