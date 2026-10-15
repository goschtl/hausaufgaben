"""Vokabeln im phase6-Account eines Kindes zum Lernen freischalten.

Inoffizielle Schnittstelle der Web-App (lernen.phase-6.de). Kann sich jederzeit
ändern — Fehler hier dürfen den Hausaufgaben-Versand nie aufhalten.

Zugang je Kind in der .env:  P6_USER_FERDINAND / P6_PASSWORD_FERDINAND
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from typing import Any

import requests

LOGIN = "https://www.phase-6.de/session/login-web"
API = "https://lernen.phase-6.de/server.integration/"
# Karten haben Deutsch als Frage, Englisch als Antwort: NORMAL = Deutsch→Englisch.
DIRECTION, DIRECTION_KEY = "NORMAL", "normal"


class Phase6Error(RuntimeError):
    pass


def credentials(student: str) -> tuple[str, str] | None:
    key = student.upper()
    user = os.environ.get(f"P6_USER_{key}")
    password = os.environ.get(f"P6_PASSWORD_{key}")
    return (user, password) if user and password else None


class Phase6:
    def __init__(self, email: str, password: str, timeout: int = 20):
        self.email = email
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0 (hausaufgaben-bot)"
        self.headers: dict[str, str] = {}

    # --------------------------------------------------------------- login

    def login(self) -> None:
        response = self.session.post(LOGIN, data={
            "josso_cmd": "login",
            "josso_username": self.email,
            "josso_password": self.password,
            "josso_back_to": "https://www.phase-6.de/classic/service/user/login-redirect-on-success.jsp",
            "josso_on_error": "https://www.phase-6.de/classic/service/user/login.html?ErrorCode=user.login.wrong",
        }, timeout=self.timeout)
        josso = self.session.cookies.get("JOSSO_SESSIONID", domain=".phase-6.de")
        if "ErrorCode" in response.url or not josso:
            raise Phase6Error("phase6-Login fehlgeschlagen (Zugangsdaten prüfen)")

        info = self.session.get(API + "jossoInfo", headers={
            "X-JAUTHTOKEN": josso, "X-LBTOKEN": self.email.lower(),
            "Accept": "application/json",
        }, timeout=self.timeout).json()
        token = (info.get("replyContent") or {}).get("p6pSessionToken")
        if not token:
            raise Phase6Error(f"phase6 lieferte kein Sitzungstoken: {info.get('httpCode')}")
        self.headers = {
            "X-JAUTHTOKEN": token,
            "X-CLIENTID": str(uuid.uuid4()),
            "X-LBTOKEN": self.email.lower(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(API + endpoint, json=body,
                                     headers=self.headers, timeout=self.timeout)
        if response.status_code >= 400:
            raise Phase6Error(f"{endpoint}: HTTP {response.status_code}")
        data = response.json()
        if data.get("httpCode") not in (None, 200):
            raise Phase6Error(f"{endpoint}: {data.get('httpCode')} {data.get('exceptionMessage', '')}")
        return data.get("replyContent") or {}

    # ---------------------------------------------------------------- data

    def units(self, subject_id: str) -> dict[str, dict[str, str]]:
        """Unit-Name („Unit 1 | pp. 8/9“) → unitId-Objekt."""
        reply = self._post("unitsFiltered", {"subjectId": subject_id, "filterMode": "LIBRARY"})
        return {u["unitContent"]["name"]: u["unitId"] for u in reply.get("units", [])}

    def cards(self, subject_id: str, unit_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            reply = self._post("cardList", {
                "filterMode": "LIBRARY", "subjectId": subject_id, "units": unit_ids,
                "phases": [], "textContains": "", "offset": offset, "limit": 100,
            })
            batch = reply.get("cards", [])
            out += batch
            if len(batch) < 100:
                return out
            offset += 100

    def set_active(self, subject_id: str, card_ids: list[str], direction: str,
                   active: bool = True) -> None:
        if card_ids:
            self._post("cardActivationFiltered", {
                "cardActivationData": {"activate": active, "direction": direction},
                "cards": card_ids, "subjectId": subject_id,
            })

    def sync_words(self, subject_id: str, unit_names: list[str],
                   words: list[str]) -> tuple[int, int]:
        """Wie activate_words, deaktiviert aber zusätzlich alle anderen Karten
        dieser Units (Deutsch→Englisch). Für den Handbetrieb nach dem
        Bearbeiten einer Wortliste. Gibt (aktiviert, deaktiviert) zurück."""
        added = self.activate_words(subject_id, unit_names, words)
        by_name = self.units(subject_id)
        wanted = set().union(*(match_keys(w) for w in words))
        cards = self.cards(subject_id, [by_name[n]["id"] for n in unit_names])
        drop = [c["cardIdToOwner"]["id"] for c in cards
                if (c.get(DIRECTION_KEY) or {}).get("active")
                and not match_keys(c["cardContent"]["answer"]) & wanted]
        self.set_active(subject_id, drop, DIRECTION, active=False)
        return added, len(drop)

    def activate_words(self, subject_id: str, unit_names: list[str],
                       words: list[str]) -> int:
        """Karten dieser Units freischalten, deren englische Seite in `words`
        steht (die Hauptwörter der Wortliste im Buch). Nur Deutsch→Englisch,
        nur noch nicht aktive Karten — Lernfortschritt bleibt unberührt.
        Gibt die Zahl neu freigeschalteter Karten zurück."""
        by_name = self.units(subject_id)
        missing = [n for n in unit_names if n not in by_name]
        if missing:
            raise Phase6Error(f"Units nicht gefunden: {', '.join(missing)}")
        cards = self.cards(subject_id, [by_name[n]["id"] for n in unit_names])

        wanted = set().union(*(match_keys(w) for w in words))
        matching = [c for c in cards if match_keys(c["cardContent"]["answer"]) & wanted]
        found = set().union(set(), *(match_keys(c["cardContent"]["answer"]) for c in matching))
        unmatched = {normalize(w) for w in words if not match_keys(w) & found}
        if unmatched:
            print(f"phase6: keine Karte für {', '.join(sorted(unmatched))}", file=sys.stderr)

        todo = [c["cardIdToOwner"]["id"] for c in matching
                if not (c.get(DIRECTION_KEY) or {}).get("active")]
        self.set_active(subject_id, todo, DIRECTION)
        return len(todo)


def normalize(text: str) -> str:
    """„(to) bring sb. Up[{~…:audio~}]“ → „(to) bring sb. up“."""
    return re.sub(r"\s+", " ", re.sub(r"\[\{.*?\}\]", "", text)).strip().lower()


def match_keys(text: str) -> set[str]:
    """Schreibweisen, unter denen ein Wort aus Buch oder phase6 gleich gilt:
    „hero, pl heroes“ ~ „hero“/„heroes“, „gobbledygook (infml)“ ~ „gobbledygook“,
    „one scarf“ ~ „scarf“, „(to) introduce sb. to sb./sth.“ ~ beide Varianten."""
    base = normalize(text)
    keys = {base}
    plain = re.sub(r"\s*\((?:infml|ae|be|pl)\)", "", base).strip()
    keys.add(plain)
    for part in re.split(r",\s*pl\s+|\s+or\s+", plain):
        keys.add(part.strip())
    if plain.startswith("one "):
        keys.add(plain[4:])
    slash = re.search(r"(\S+)/(\S+)$", plain)
    if slash:
        keys |= {plain[:slash.start()] + alt for alt in slash.groups()}
    return {k for k in keys if k}
