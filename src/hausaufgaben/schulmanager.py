"""Minimaler Client für die (inoffizielle) Schulmanager-Online-API.

Kein offizielles API. Endpunkte und Feldnamen wurden von der Community
aus der Weboberfläche abgeleitet und können sich jederzeit ändern.
"""

from __future__ import annotations

import hashlib
from typing import Any

import requests

BASE = "https://login.schulmanager-online.de"

# Die Weboberfläche schickt ihre Bundle-Version mit. Ändert sich gelegentlich;
# falls /api/calls plötzlich 4xx liefert, im Browser-DevTools den aktuellen
# Wert aus einem /api/calls-Request abgreifen und hier eintragen.
BUNDLE_VERSION = "3505280ee7"

PBKDF2_ITERATIONS = 99_999
PBKDF2_DKLEN = 512


class SchulmanagerError(RuntimeError):
    pass


class Schulmanager:
    def __init__(self, username: str, password: str, timeout: int = 20):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                # Fair sein: sich identifizieren, damit der Betreiber den
                # Traffic zuordnen kann, falls er mal nachschaut.
                "User-Agent": "hausaufgaben-bot/1.0 (privater Elternaccount)",
                "Content-Type": "application/json",
            }
        )
        self.token: str | None = None
        self.user: dict[str, Any] = {}

    # ---------------------------------------------------------------- login

    def login(self) -> None:
        salt = self._post("/api/get-salt", {
            "emailOrUsername": self.username,
            "mobileApp": False,
        })
        if isinstance(salt, dict):
            salt = salt.get("salt", "")

        hashed = hashlib.pbkdf2_hmac(
            "sha512",
            self.password.encode("utf-8"),
            str(salt).encode("utf-8"),
            PBKDF2_ITERATIONS,
            dklen=PBKDF2_DKLEN,
        ).hex()

        data = self._post("/api/login", {
            "emailOrUsername": self.username,
            "password": self.password,
            "hash": hashed,
            "mobileApp": False,
        })

        self.token = data.get("jwt") or data.get("token")
        if not self.token:
            raise SchulmanagerError(f"Kein Token in der Login-Antwort: {data!r}")

        self.user = data.get("user", {})
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    # ------------------------------------------------------------- students

    def students(self) -> list[dict[str, Any]]:
        """Schülerdatensätze — funktioniert für Eltern- wie Schüleraccounts."""
        if self.user.get("associatedStudent"):
            return [self.user["associatedStudent"]]

        out = []
        for parent in self.user.get("associatedParents", []) or []:
            student = parent.get("student")
            if student:
                out.append(student)
        return out

    # ----------------------------------------------------------------- data

    def call(self, module: str, endpoint: str,
             parameters: dict[str, Any] | None = None) -> Any:
        body = {
            "bundleVersion": BUNDLE_VERSION,
            "requests": [{
                "moduleName": module,
                "endpointName": endpoint,
                "parameters": parameters or {},
            }],
        }
        payload = self._post("/api/calls", body)
        results = payload.get("results") or []
        if not results:
            raise SchulmanagerError(f"Leere Antwort für {module}/{endpoint}")

        result = results[0]
        status = result.get("status")
        if status not in (None, 200, "success"):
            raise SchulmanagerError(f"{module}/{endpoint} → {result!r}")
        return result.get("data")

    def homework(self, student_id: int) -> list[dict[str, Any]]:
        data = self.call("classbook", "get-homework",
                         {"student": {"id": student_id}})
        if isinstance(data, dict):
            # Manche Deployments verpacken die Liste noch einmal.
            for key in ("homework", "homeworks", "items", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data or []

    def lessons(self, student_id: int, start: str, end: str) -> list[dict[str, Any]]:
        """Tatsächliche Stunden (inkl. Vertretungen) zwischen start und end (ISO-Daten)."""
        data = self.call("schedules", "get-actual-lessons",
                         {"student": {"id": student_id}, "start": start, "end": end})
        return data if isinstance(data, list) else []

    # -------------------------------------------------------------- interna

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = self.session.post(BASE + path, json=body, timeout=self.timeout)
        if response.status_code >= 400:
            raise SchulmanagerError(
                f"POST {path} → HTTP {response.status_code}: {response.text[:400]}"
            )
        return response.json()
