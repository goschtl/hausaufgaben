"""Vokabeln zu Aufgaben wie „vocabulary p. 147/148“ aus lokalen Listen anhängen.

Aufbau des Ordners (Standard: ./vokabeln, änderbar über HA_VOKABELN):

    vokabeln/buecher.json          {"Ferdinand": {"Englisch": {"ordner": "go-ahead-9",
                                                               "phase6": "<subject-id>"}}}
    vokabeln/go-ahead-9/147.txt    eine Vokabel pro Zeile, z. B. „ship – Schiff“
    vokabeln/go-ahead-9/seiten.json  {"147": ["Unit 1 | pp. 8/9"]}  (nur für phase6)

Statt des Objekts reicht auch nur der Ordnername als Text.

Fehlt eine Seite, wird das im Log vermerkt und die Nachricht geht ohne sie raus.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

VOKABELN = Path(os.environ.get("HA_VOKABELN", Path.cwd() / "vokabeln"))

# Nur Aufgaben, die wirklich nach Vokabeln klingen.
KEYWORDS = re.compile(r"(?i)vocab|vokabel|\bwords\b|wörter|wortschatz")
# „p. 147“, „pp. 147-149“, „page 12“, „S. 16/17“, „Seite 147f“, „p.147 & 148“
PAGES = re.compile(
    r"(?i)(?:\bpp?\.|\bpages?\b|\bS\.|\bSeiten?\b)\s*"
    r"(\d{1,3}(?:\s*(?:/|-|–|,|&|\+|und|and)\s*\d{1,3})*)\s*(ff?\b)?"
)


def pages_in(text: str) -> list[int]:
    """Seitenzahlen aus einem Aufgabentext, nur wenn es um Vokabeln geht."""
    if not KEYWORDS.search(text):
        return []
    pages: list[int] = []
    for match in PAGES.finditer(text):
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        if re.search(r"[-–]", match.group(1)) and len(numbers) == 2 and numbers[0] <= numbers[1] <= numbers[0] + 10:
            numbers = list(range(numbers[0], numbers[1] + 1))
        if match.group(2):  # „147f“ / „147ff“ → Folgeseite dazu
            numbers.append(numbers[-1] + 1)
        pages.extend(n for n in numbers if n not in pages)
    return pages


def _books() -> dict[str, dict[str, Any]]:
    path = VOKABELN / "buecher.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError as exc:
        print(f"{path} ist kein gültiges JSON: {exc}", file=sys.stderr)
        return {}


def book(student: str, subject: str) -> dict[str, Any] | None:
    """Buch-Konfiguration: {"ordner": ..., "phase6": subject-id oder None}."""
    entry = _books().get(student, {}).get(subject)
    if isinstance(entry, str):
        return {"ordner": entry, "phase6": None}
    if isinstance(entry, dict) and entry.get("ordner"):
        return entry
    return None


def phase6_units(folder: str, pages: list[int]) -> list[str]:
    """phase6-Unit-Namen zu Wortlisten-Seiten laut seiten.json."""
    path = VOKABELN / folder / "seiten.json"
    try:
        mapping = json.loads(path.read_text()) if path.exists() else {}
    except ValueError as exc:
        print(f"{path} ist kein gültiges JSON: {exc}", file=sys.stderr)
        return []
    units: list[str] = []
    for page in pages:
        if str(page) not in mapping:
            print(f"Keine phase6-Zuordnung für S. {page} in {path}", file=sys.stderr)
        for unit in mapping.get(str(page), []):
            if unit not in units:
                units.append(unit)
    return units


def lookup(student: str, subject: str, pages: list[int]) -> dict[int, list[str]]:
    """Vokabeln je Seite; Seiten ohne Datei fehlen im Ergebnis."""
    config = book(student, subject)
    if not config or not pages:
        return {}
    found: dict[int, list[str]] = {}
    for page in pages:
        path = VOKABELN / config["ordner"] / f"{page}.txt"
        if not path.exists():
            print(f"Vokabeln fehlen: {path}", file=sys.stderr)
            continue
        words = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        if words:
            found[page] = words
    return found


def page_label(pages: list[int]) -> str:
    if len(pages) > 1 and pages == list(range(pages[0], pages[-1] + 1)):
        return f"S. {pages[0]}–{pages[-1]}"
    return "S. " + ", ".join(map(str, pages))


# ---------- Rechte Spalte: Gegenteile, Reihen, Wortfamilien, Wendungen ----------
#
#   vokabeln/<buch>/<seite>.rechts.txt, eine Zeile je Eintrag, Felder mit „ | “:
#       gegenteil | beginning | end
#       reihe | the past | the present | the future
#       familie | verb: (to) dream | noun: dream | Traum
#       wendung | in a loud voice | mit lauter Stimme
#       erklaerung | convict | somebody who has been sent to prison
#
# Daraus werden Abfragekarten {"frage", "antwort", "art"}; „antwort“ ist immer Englisch.

WORTART = {"noun": "Nomen", "verb": "Verb", "adjective": "Adjektiv", "adverb": "Adverb"}


def _ohne_to(text: str) -> str:
    return re.sub(r"^\(to\)\s*", "", text).strip().lower()


def rechte_spalte_karten(line: str) -> list[dict[str, str]]:
    fields = [f.strip() for f in line.split(" | ")]
    art, rest = fields[0], fields[1:]
    if art == "gegenteil" and len(rest) == 2:
        a, b = rest
        return [{"frage": f"Gegenteil von „{a}“", "antwort": b, "art": art},
                {"frage": f"Gegenteil von „{b}“", "antwort": a, "art": art}]
    if art == "reihe" and len(rest) >= 3:
        return [{"frage": " – ".join("?" if j == i else w for j, w in enumerate(rest)), "antwort": rest[i], "art": art}
                for i in range(len(rest))]
    if art == "familie" and len(rest) >= 2:
        (pos_a, _, a), (pos_b, _, b) = (f.partition(": ") for f in rest[:2])
        bedeutung = f" ({rest[2]})" if len(rest) > 2 else ""
        if _ohne_to(a) == _ohne_to(b):  # hurt – (to) hurt: nur die Bedeutung abfragen
            return [{"frage": f"{WORTART.get(pos_b, pos_b)}{bedeutung}, gleich geschrieben wie das {WORTART.get(pos_a, pos_a)} „{a}“", "antwort": b, "art": art}]
        return [{"frage": f"{WORTART.get(pos_b, pos_b)} zu „{a}“{bedeutung}", "antwort": b, "art": art},
                {"frage": f"{WORTART.get(pos_a, pos_a)} zu „{b}“", "antwort": a, "art": art}]
    if art == "wendung" and len(rest) == 2:
        return [{"frage": rest[1], "antwort": rest[0], "art": art}]
    if art == "erklaerung" and len(rest) == 2:
        return [{"frage": f"Welches Wort ist gemeint? „{rest[1]}“", "antwort": rest[0], "art": art}]
    print(f"Rechte Spalte: Zeile nicht verstanden: {line!r}", file=sys.stderr)
    return []


def rechte_spalte(folder: str, page: int | str) -> list[dict[str, str]]:
    path = VOKABELN / folder / f"{page}.rechts.txt"
    if not path.exists():
        return []
    cards: list[dict[str, str]] = []
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            cards.extend(rechte_spalte_karten(line))
    return cards
