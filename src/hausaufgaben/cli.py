"""Neue Hausaufgaben aus Schulmanager Online in eine WhatsApp-Gruppe posten.

Läuft einmal täglich, schickt nur, was seit dem letzten Lauf dazugekommen ist,
meldet Mo–Fr auch, wenn nichts Neues auf ist, und schweigt am Wochenende ohne Neues.

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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import vokabeln
from .schulmanager import Schulmanager, SchulmanagerError

STATE = Path(os.environ.get("HA_STATE", Path.home() / ".hausaufgaben-state.json"))
MAX_REMEMBERED = 500
SCHEDULE_DAYS = 28  # so weit voraus nach der nächsten Stunde suchen (Ferien!)

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


def hw_date(item: dict[str, Any]) -> date | None:
    """Tag der Stunde, in der die Aufgabe aufgegeben wurde (nicht der Abgabetermin)."""
    for key in ("date", "homeworkDate"):
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


# -------------------------------------------------------------- Stundenplan

Schedule = dict[str, list[date]]  # Fachname → Tage mit Unterricht, aufsteigend


def build_schedule(lessons: list[dict[str, Any]]) -> Schedule:
    """Ausfälle tragen kein actualLesson und fallen damit von selbst heraus."""
    days: dict[str, set[date]] = {}
    for lesson in lessons:
        subject = ((lesson.get("actualLesson") or {}).get("subject") or {}).get("name")
        try:
            day = date.fromisoformat(str(lesson.get("date"))[:10])
        except ValueError:
            continue
        if subject:
            days.setdefault(subject, set()).add(day)
    return {subject: sorted(found) for subject, found in days.items()}


def next_lesson(item: dict[str, Any], schedule: Schedule) -> date | None:
    """Erste Stunde des Fachs nach dem Aufgabetag — dann ist die Aufgabe fällig."""
    given = hw_date(item) or date.today()
    return next((day for day in schedule.get(hw_subject(item), []) if day > given), None)


def fetch_lessons(client: Schulmanager, student_id: int, name: str) -> list[dict[str, Any]]:
    today = date.today()
    try:
        return client.lessons(student_id, today.isoformat(),
                              (today + timedelta(days=SCHEDULE_DAYS)).isoformat())
    except SchulmanagerError as exc:
        print(f"Stundenplan für {name} fehlgeschlagen: {exc}", file=sys.stderr)
        return []


def _subject(lesson: dict[str, Any]) -> str:
    subject = lesson.get("subject") or {}
    return subject.get("name") or lesson.get("subjectLabel") or "?"


def _teachers(lesson: dict[str, Any]) -> str:
    return ", ".join(t.get("lastname") or t.get("abbreviation") or "?"
                     for t in lesson.get("teachers") or [])


def _room(lesson: dict[str, Any]) -> str:
    return (lesson.get("room") or {}).get("name") or ""


def describe_change(lesson: dict[str, Any]) -> str:
    """Eine Vertretung/einen Ausfall in Worten — nur was sich wirklich ändert."""
    actual = lesson.get("actualLesson")
    originals = lesson.get("originalLessons") or []
    original = originals[0] if originals else None
    comment = lesson.get("comment") or (actual or {}).get("comment")

    if not actual or "cancel" in str(lesson.get("type")).lower():
        text = f"_{_subject(original or actual or {})}_ entfällt"
    elif not original:
        text = f"_{_subject(actual)}_ zusätzlich"
        if _teachers(actual):
            text += f" bei {_teachers(actual)}"
        if _room(actual):
            text += f", Raum {_room(actual)}"
    else:
        text = f"_{_subject(actual)}_"
        if _subject(actual) != _subject(original):
            text += f" statt _{_subject(original)}_"
        details = []
        if _teachers(actual) != _teachers(original):
            details.append(f"bei {_teachers(actual) or '—'} (statt {_teachers(original) or '—'})")
        if _room(actual) != _room(original):
            details.append(f"Raum {_room(actual) or '—'} (statt {_room(original) or '—'})")
        text += " " + ", ".join(details) if details else " geändert"
    if comment:
        text += f" – {' '.join(str(comment).split())}"
    return text


def changes_on(lessons: list[dict[str, Any]], day: date) -> list[str]:
    """Vertretungen/Ausfälle eines Tages, gleiche Änderungen über Stunden zusammengefasst."""
    hours: dict[str, list[str]] = {}
    for lesson in lessons:
        if str(lesson.get("date"))[:10] != day.isoformat():
            continue
        if lesson.get("type") == "regularLesson" and not lesson.get("isSubstitution"):
            continue
        number = str((lesson.get("classHour") or {}).get("number") or "?")
        hours.setdefault(describe_change(lesson), []).append(number)

    def first_hour(entry: tuple[str, list[str]]) -> int:
        return min((int(h) for h in entry[1] if h.isdigit()), default=99)

    return [f"• {'/'.join(f'{x}.' for x in sorted(h, key=lambda x: (len(x), x)))} Std.: {text}"
            for text, h in sorted(hours.items(), key=first_hour)]


def changes_block(changes: dict[str, list[str]]) -> list[str]:
    """Vertretungen für morgen, je Kind."""
    if not any(changes.values()):
        return []
    tomorrow = date.today() + timedelta(days=1)
    lines = [f"🔄 *Vertretungen morgen* ({fmt_date(tomorrow)})"]
    for student_name, entries in changes.items():
        if entries:
            lines += [f"_{student_name}_", *entries]
    return lines


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

Unlocked = dict[tuple[str, str], int]  # (Kind, Fach) → neu freigeschaltete Karten


def build_message(by_student: dict[str, list[dict[str, Any]]],
                  unlocked: Unlocked | None = None,
                  schedules: dict[str, Schedule] | None = None,
                  changes: dict[str, list[str]] | None = None) -> str:
    today = date.today()
    lines = [f"*Neue Hausaufgaben* – {fmt_date(today)}", ""]

    for student_name, items in by_student.items():
        schedule = (schedules or {}).get(student_name, {})
        lines.append(f"*{student_name}*")
        due = {id(item): next_lesson(item, schedule) for item in items}
        for item in sorted(items, key=lambda i: (due[id(i)] or date.max, hw_subject(i))):
            when = (f"→ {fmt_date(due[id(item)])}" if due[id(item)]
                    else f"vom {fmt_date(hw_date(item))}")
            lines.append(f"• _{hw_subject(item)}_ ({when}): {hw_text(item)}")
        lines.extend(vocab_lines(student_name, items, unlocked or {}))
        lines.append("")

    lines.extend(changes_block(changes or {}))
    return "\n".join(lines).strip()


def vocab_pages(items: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Fach → Wortlisten-Seiten aus Aufgaben wie „vocabulary p. 147/148“."""
    wanted: dict[str, list[int]] = {}
    for item in items:
        subject = hw_subject(item)
        for page in vokabeln.pages_in(hw_text(item)):
            if page not in wanted.setdefault(subject, []):
                wanted[subject].append(page)
    return {subject: sorted(pages) for subject, pages in wanted.items() if pages}


def vocab_lines(student_name: str, items: list[dict[str, Any]],
                unlocked: Unlocked) -> list[str]:
    """Vokabelblöcke je Fach, plus Hinweis auf die phase6-Freischaltung."""
    lines: list[str] = []
    for subject, pages in vocab_pages(items).items():
        found = vokabeln.lookup(student_name, subject, pages)
        count = unlocked.get((student_name, subject), 0)
        if not found and not count:
            continue
        label = vokabeln.page_label(sorted(found) or pages)
        lines += ["", f"📚 _Vokabeln {subject}, {label}_"]
        for words in found.values():
            lines += words
        if count:
            lines.append(f"✅ In phase6 freigeschaltet ({count} neue Karten)")
    return lines


def unlock_phase6(by_student: dict[str, list[dict[str, Any]]]) -> Unlocked:
    """Vokabeln im phase6-Account der Kinder freischalten. Fehler nur loggen."""
    from . import phase6

    unlocked: Unlocked = {}
    for student_name, items in by_student.items():
        creds = phase6.credentials(student_name)
        client = None
        for subject, pages in vocab_pages(items).items():
            config = vokabeln.book(student_name, subject)
            if not creds or not config or not config.get("phase6"):
                continue
            units = vokabeln.phase6_units(config["ordner"], pages)
            found = vokabeln.lookup(student_name, subject, pages)
            words = [line.split(" – ")[0] for lines in found.values() for line in lines]
            if not units or not words:
                continue
            try:
                if client is None:
                    client = phase6.Phase6(*creds)
                    client.login()
                unlocked[(student_name, subject)] = client.activate_words(
                    config["phase6"], units, words)
                print(f"phase6 {student_name}/{subject}: {unlocked[(student_name, subject)]} "
                      f"Karten freigeschaltet ({', '.join(units)})")
            except (phase6.Phase6Error, OSError, ValueError, KeyError) as exc:
                print(f"phase6 {student_name}/{subject} fehlgeschlagen: {exc}", file=sys.stderr)
    return unlocked


def build_empty_message(changes: dict[str, list[str]] | None = None) -> str:
    lines = [f"*Hausaufgaben* – {fmt_date(date.today())}", "", "Heute nichts Neues auf 🎉"]
    block = changes_block(changes or {})
    if block:
        lines += ["", *block]
    return "\n".join(lines)


def phase6_sync(student: str, subject: str, pages: list[int]) -> int:
    """Handbetrieb: phase6 exakt auf die Wörter dieser Wortlisten-Seiten setzen."""
    from . import phase6

    config = vokabeln.book(student, subject)
    creds = phase6.credentials(student)
    if not pages or not config or not config.get("phase6") or not creds:
        print(f"Für {student}/{subject} fehlen Seiten, Buch-Eintrag mit phase6 oder "
              f"P6_USER_{student.upper()}/P6_PASSWORD_{student.upper()}.", file=sys.stderr)
        return 2
    units = vokabeln.phase6_units(config["ordner"], pages)
    found = vokabeln.lookup(student, subject, pages)
    words = [line.split(" – ")[0] for lines in found.values() for line in lines]
    if not units or not words:
        print("Keine Units oder keine Wörter für diese Seiten.", file=sys.stderr)
        return 1
    client = phase6.Phase6(*creds)
    try:
        client.login()
        added, dropped = client.sync_words(config["phase6"], units, words)
    except phase6.Phase6Error as exc:
        print(f"phase6 fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print(f"{student}: {len(words)} Wörter in der Liste, {added} freigeschaltet, "
          f"{dropped} deaktiviert ({', '.join(units)})")
    return 0


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
    parser.add_argument("--phase6-sync", nargs="+", metavar=("KIND", "SEITE"),
                        help="phase6 an die Wortlisten angleichen, z. B. --phase6-sync Ferdinand 147 148")
    parser.add_argument("--fach", default="Englisch", help="Fach für --phase6-sync")
    args = parser.parse_args()

    if args.phase6_sync:
        return phase6_sync(args.phase6_sync[0], args.fach, [int(x) for x in args.phase6_sync[1:]])

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
    schedules: dict[str, Schedule] = {}
    # Nur „morgen“: freitags kommt Montag dran, wenn der Sonntagslauf ihn meldet.
    changes: dict[str, list[str]] = {}
    tomorrow = date.today() + timedelta(days=1)
    fetch_failed = False

    for student in students:
        name = student.get("firstname") or f"Kind {student.get('id')}"
        try:
            items = client.homework(student["id"])
        except SchulmanagerError as exc:
            print(f"Abruf für {name} fehlgeschlagen: {exc}", file=sys.stderr)
            fetch_failed = True
            continue

        new_items = []
        for item in items:
            identifier = f"{student['id']}:{hw_id(item)}"
            if identifier in seen:
                continue
            new_items.append(item)
            fresh_ids.add(identifier)

        lessons = fetch_lessons(client, student["id"], name)
        schedules[name] = build_schedule(lessons)
        changes[name] = changes_on(lessons, tomorrow)

        if new_items:
            by_student[name] = new_items

    if by_student:
        # Im Trockenlauf nichts in phase6 verändern.
        unlocked = {} if args.dry_run else unlock_phase6(by_student)
        message = build_message(by_student, unlocked, schedules, changes)
    elif date.today().weekday() >= 5 and not any(changes.values()):
        print("Nichts Neues und Wochenende – keine Nachricht gesendet.")
        return 0
    elif fetch_failed:
        # Nicht fälschlich "nichts auf" melden, wenn ein Abruf gescheitert ist.
        print("Nichts Neues, aber Abruf unvollständig – keine Nachricht gesendet.")
        return 1
    else:
        message = build_empty_message(changes)

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
