"""Vokabeln sprechen üben: kleiner lokaler Webserver mit Spracherkennung im Browser.

    uv run vokabeln-sprechen            # öffnet http://localhost:8765

Die Wortlisten kommen aus vokabeln/ (siehe vokabeln.py), jeder Versuch wird als
Tonaufnahme in aufnahmen/<datum>/ abgelegt (änderbar über HA_AUFNAHMEN).
Der Server hört nur auf localhost – das Mikrofon gibt der Browser nur dort frei.
Mit OPENAI_API_KEY (Umgebung oder .env) bewertet zusätzlich ein Audiomodell die
Aussprache, siehe aussprache.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import aussprache, vokabeln

PORT = int(os.environ.get("HA_SPRECHEN_PORT", "8765"))
AUFNAHMEN = Path(os.environ.get("HA_AUFNAHMEN", Path.cwd() / "aufnahmen"))
PAGE = Path(__file__).with_name("sprechen.html")
MAX_UPLOAD = 10 * 1024 * 1024
AUDIO_TYPES = {".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".mp4": "audio/mp4"}


def word_lists() -> dict[str, dict[str, list[dict[str, str]]]]:
    """{"Jakob": {"167": [{"en": ..., "de": ...}, ...]}} für alle Kinder mit Englischbuch."""
    out: dict[str, dict[str, list[dict[str, str]]]] = {}
    for student in vokabeln._books():
        config = vokabeln.book(student, "Englisch")
        if not config:
            continue
        folder = vokabeln.VOKABELN / config["ordner"]
        pages: dict[str, list[dict[str, str]]] = {}
        for path in sorted(folder.glob("*.txt"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
            if not path.stem.isdigit():
                continue
            words = []
            for line in path.read_text().splitlines():
                en, sep, de = line.partition(" – ")
                if sep and en.strip():
                    words.append({"en": en.strip(), "de": de.strip()})
            words += [{"en": c["antwort"], "de": c["frage"], "art": c["art"]}
                      for c in vokabeln.rechte_spalte(config["ordner"], path.stem)]
            if words:
                pages[path.stem] = words
        if pages:
            out[student] = pages
    return out


def load_env(path: Path | None = None) -> None:
    """„export KEY=wert“-Zeilen aus .env übernehmen, ohne Gesetztes zu überschreiben."""
    path = path or Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep and key.strip().isidentifier():
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def recording(datei: str) -> Path | None:
    """Pfad einer Aufnahme aus „datum/name.webm“, nur innerhalb von AUFNAHMEN."""
    path = (AUFNAHMEN / unquote(datei)).resolve()
    if AUFNAHMEN.resolve() in path.parents and path.suffix in AUDIO_TYPES and path.is_file():
        return path
    return None


def update_meta(audio: Path, **changes) -> dict:
    meta_path = audio.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update(changes)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def slug(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "wort"


def recordings() -> list[dict[str, str]]:
    """Alle Aufnahmen, neueste zuerst, mit den Angaben aus der .json daneben."""
    items = []
    for audio in AUFNAHMEN.glob("*/*"):
        if audio.suffix not in AUDIO_TYPES:
            continue
        meta_path = audio.with_suffix(".json")
        try:
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        except ValueError:
            meta = {}
        items.append({**meta, "datei": f"{audio.parent.name}/{audio.name}"})
    return sorted(items, key=lambda i: i["datei"], reverse=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # ruhig bleiben, nur Fehler zeigen
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    def send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status: int = 200) -> None:
        self.send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/vokabeln.json":
            self.send_json(word_lists())
        elif url.path == "/aufnahmen.json":
            self.send_json(recordings())
        elif url.path == "/status.json":
            self.send_json({"ki": aussprache.verfuegbar(), "modell": aussprache.MODELL})
        elif url.path.startswith("/aufnahmen/"):
            if path := recording(url.path.removeprefix("/aufnahmen/")):
                self.send(200, path.read_bytes(), AUDIO_TYPES[path.suffix])
            else:
                self.send(404, b"nicht gefunden", "text/plain")
        else:
            self.send(404, b"nicht gefunden", "text/plain")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/bewerten", "/urteil"):
            audio = recording(query.get("datei", ""))
            if not audio:
                return self.send_json({"fehler": "Aufnahme nicht gefunden"}, 404)
            if url.path == "/urteil":
                return self.send_json(update_meta(audio, richtig=query.get("ok") == "1"))
            meta = json.loads(audio.with_suffix(".json").read_text())
            try:
                bewertung = aussprache.bewerten(audio, meta.get("en", ""), meta.get("de", ""))
            except Exception as exc:  # noqa: BLE001 – Netz, Schlüssel, ffmpeg – im Spiel nur als Hinweis zeigen
                print(f"KI-Bewertung fehlgeschlagen: {exc!r}", file=sys.stderr)
                return self.send_json({"fehler": "Die KI-Bewertung hat gerade nicht geklappt."}, 502)
            update_meta(audio, ki=bewertung.model_dump())
            return self.send_json(bewertung.model_dump())
        if url.path != "/aufnahme":
            return self.send(404, b"nicht gefunden", "text/plain")
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_UPLOAD:
            return self.send(413, b"zu gross", "text/plain")
        ctype = self.headers.get("Content-Type", "")
        suffix = ".m4a" if "mp4" in ctype else ".ogg" if "ogg" in ctype else ".webm"
        now = datetime.now()
        folder = AUFNAHMEN / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        result = "richtig" if query.get("ok") == "1" else "falsch"
        name = f"{now:%H%M%S}_{slug(query.get('kind', ''))}_{slug(query.get('en', ''))}"
        (folder / name).with_suffix(suffix).write_bytes(self.rfile.read(size))
        meta = {
            "zeit": now.isoformat(timespec="seconds"),
            "kind": query.get("kind", ""),
            "seite": query.get("seite", ""),
            "en": query.get("en", ""),
            "de": query.get("de", ""),
            "gehoert": query.get("gehoert", ""),
            "richtig": result == "richtig",
        }
        (folder / name).with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        self.send_json({"datei": f"{folder.name}/{name}{suffix}"})


def main() -> int:
    load_env()
    if not word_lists():
        print(f"Keine Wortlisten gefunden in {vokabeln.VOKABELN}", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    address = f"http://localhost:{PORT}"
    ki = f"KI-Aussprache: {aussprache.MODELL}" if aussprache.verfuegbar() else "KI-Aussprache aus (kein OPENAI_API_KEY)"
    print(f"Vokabeln sprechen: {address}  (Aufnahmen in {AUFNAHMEN}, {ki})  – Beenden mit Ctrl+C", flush=True)
    if "--kein-browser" not in sys.argv:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
