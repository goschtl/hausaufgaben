"""Neue Hausaufgaben aus Schulmanager Online in eine WhatsApp-Gruppe posten.

Läuft einmal täglich, schickt nur, was seit dem letzten Lauf dazugekommen ist,
und schweigt an Tagen ohne Neues.

    hausaufgaben --list-groups   einmalig: WhatsApp koppeln, Gruppen-JIDs anzeigen
    hausaufgaben --dump          einmalig: Rohdaten der Schulmanager-API ansehen
    hausaufgaben --dry-run       Nachricht bauen, aber nicht senden
    hausaufgaben                 der eigentliche Lauf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .schulmanager import Schulmanager, SchulmanagerError

STATE = Path(os.environ.get("HA_STATE", Path.home() / ".hausaufgaben-state.json"))
MAX_REMEMBERED = 500

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# --------------------------------------------------------------- Feldzugriff
# Die Feldnamen der inoffiziellen API sind nicht garantiert. Deshalb hier
# überall mehrere Kandidaten probieren — und einmal --dump laufen lassen,
# falls etwas leer bleibt.

def hw_id(item: dict[str, Any]) -> str:
    for key in ("id", "homeworkId", "uuid"):
        if item.get(key) not in (None, ""):
            return str(item[key])
    # Fallback: inhaltlicher Fingerabdruck
    return json.dumps(item, sort_keys=True, ensure_ascii=False)[:200]


def hw_subject(item: dict[str, Any]) -> str:
    subject = item.get("subject") or item.get("course") or {}
    if isinstance(subject, dict):
        return (subject.get("abbreviation") or subject.get("name")
                or subject.get("shortName") or "?")
    return str(subject)


def hw_text(item: dict[str, Any]) -> str:
    for key in ("homework", "text", "description", "content", "comment"):
        value = item.get(key)
        if value:
            return " ".join(str(value).split())
    return "(kein Text hinterlegt)"


def hw_due(item: dict[str, Any]) -> date | None:
    for key in ("date", "dueDate", "homeworkDate", "deadline"):
        value = item.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except ValueError:
            continue
    return None


def fmt_date(value: date | None) -> str:
    if value is None:
        return "ohne Termin"
    return f"{WEEKDAYS[value.weekday()]}, {value.strftime('%d.%m.')}"


# ------------------------------------------------------------------- State

def load_state() -> set[str]:
    if not STATE.exists():
        return set()
    try:
        return set(json.loads(STATE.read_text())["seen"])
    except (ValueError, KeyError):
        return set()


def save_state(seen: set[str]) -> None:
    trimmed = list(seen)[-MAX_REMEMBERED:]
    STATE.write_text(json.dumps({"seen": trimmed}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- Nachricht

def build_message(by_student: dict[str, list[dict[str, Any]]]) -> str:
    today = date.today()
    lines = [f"*Neue Hausaufgaben* – {fmt_date(today)}", ""]

    multiple = len(by_student) > 1
    for student_name, items in by_student.items():
        if multiple:
            lines.append(f"*{student_name}*")
        for item in sorted(items, key=lambda i: (hw_due(i) or date.max, hw_subject(i))):
            lines.append(
                f"• _{hw_subject(item)}_ (bis {fmt_date(hw_due(item))}): {hw_text(item)}"
            )
        if multiple:
            lines.append("")

    return "\n".join(lines).strip()


# --------------------------------------------------------------------- Main

def main() -> int:
    parser = argparse.ArgumentParser(prog="hausaufgaben")
    parser.add_argument("--list-groups", action="store_true",
                        help="WhatsApp koppeln (QR-Code) und Gruppen mit JID auflisten")
    parser.add_argument("--pair-phone", metavar="+49170…",
                        help="mit --list-groups: statt QR einen Kopplungscode für diese Nummer holen")
    parser.add_argument("--dump", action="store_true",
                        help="Rohdaten der API ausgeben und beenden")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nachricht bauen und ausgeben, nichts senden, State unverändert")
    args = parser.parse_args()

    if args.list_groups:
        from . import wa

        try:
            wa.list_groups(pair_phone=args.pair_phone)
        except wa.WhatsAppError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    username = os.environ.get("SM_USER")
    password = os.environ.get("SM_PASSWORD")
    group_jid = os.environ.get("WA_GROUP_JID")

    if not username or not password:
        print("SM_USER und SM_PASSWORD müssen gesetzt sein.", file=sys.stderr)
        return 2

    client = Schulmanager(username, password)
    try:
        client.login()
    except SchulmanagerError as exc:
        print(f"Login fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    students = client.students()
    if not students:
        print("Keine Schüler im Account gefunden.", file=sys.stderr)
        return 1

    if args.dump:
        for student in students:
            print(f"--- {student.get('firstname')} {student.get('lastname')} "
                  f"(id={student.get('id')}) ---")
            print(json.dumps(client.homework(student["id"]),
                             indent=2, ensure_ascii=False))
        return 0

    seen = load_state()
    fresh_ids: set[str] = set()
    by_student: dict[str, list[dict[str, Any]]] = {}

    for student in students:
        name = student.get("firstname") or f"Kind {student.get('id')}"
        try:
            items = client.homework(student["id"])
        except SchulmanagerError as exc:
            print(f"Abruf für {name} fehlgeschlagen: {exc}", file=sys.stderr)
            continue

        new_items = []
        for item in items:
            identifier = f"{student['id']}:{hw_id(item)}"
            if identifier in seen:
                continue
            new_items.append(item)
            fresh_ids.add(identifier)

        if new_items:
            by_student[name] = new_items

    if not by_student:
        print("Nichts Neues – keine Nachricht gesendet.")
        return 0

    message = build_message(by_student)

    if args.dry_run:
        print(message)
        return 0

    if not group_jid:
        print("WA_GROUP_JID ist nicht gesetzt.", file=sys.stderr)
        return 2

    from . import wa  # erst hier importieren, damit --dump ohne WhatsApp-Session läuft

    try:
        wa.send(group_jid, message)
    except wa.WhatsAppError as exc:
        print(f"WhatsApp-Versand fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    save_state(seen | fresh_ids)
    print(f"Gesendet: {len(fresh_ids)} neue Einträge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
