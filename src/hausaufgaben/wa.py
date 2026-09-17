"""WhatsApp-Versand über neonize (Python-Bindings für whatsmeow).

Klinkt sich als weiteres Linked Device in deinen bestehenden WhatsApp-Account
ein — genau wie der Mac-Client. Einmal QR scannen, danach liegt die Session in
der SQLite-Datei und der Login hält.

Hinweis: neonize muss regelmäßig aktualisiert werden (`uv lock --upgrade-package
neonize`), sonst lehnt WhatsApp den eingebetteten Client irgendwann mit
"client outdated (405)" ab. Falls nach einem Update eine Methode fehlt, steht
die aktuelle Signatur in den Docs:
https://zhannexus.github.io/neonize/
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import segno
from neonize.client import NewClient
from neonize.events import (
    ClientOutdatedEv,
    ConnectedEv,
    ConnectFailureEv,
    LoggedOutEv,
    PairStatusEv,
    StreamErrorEv,
    TemporaryBanEv,
)
from neonize.proto.Neonize_pb2 import JID
from neonize.utils.jid import Jid2String, build_jid

SESSION = Path(os.environ.get("WA_SESSION", Path.home() / ".hausaufgaben-wa.sqlite3"))
QR_FILE = SESSION.with_suffix(".qr.png")

# Wie lange auf die Verbindung gewartet wird. Beim ersten Koppeln muss der
# QR-Code gescannt werden, dafür deutlich mehr Zeit lassen.
CONNECT_TIMEOUT = int(os.environ.get("WA_TIMEOUT", "60"))
PAIR_TIMEOUT = int(os.environ.get("WA_PAIR_TIMEOUT", "300"))


class WhatsAppError(RuntimeError):
    pass


_qr_opened = False


def _show_qr(client: NewClient, data: bytes) -> None:
    """QR im Terminal und zusätzlich als PNG. Die Terminal-Blockgrafik ist je
    nach Schrift und Zeilenabstand oft nicht scannbar; das PNG wird auf dem
    Mac beim ersten Mal automatisch in der Vorschau geöffnet, spätere
    Aktualisierungen überschreiben die Datei (Vorschau lädt sie neu)."""
    global _qr_opened
    qr = segno.make_qr(data)
    qr.save(str(QR_FILE), scale=8, border=2)
    print("\n")
    qr.terminal(compact=True, border=2)
    print(f"\nQR-Code als Bild: {QR_FILE}")
    print("WhatsApp → Verknüpfte Geräte → Gerät hinzufügen. "
          "Der Code wird alle ~20 s erneuert; immer den aktuellen scannen.\n")
    sys.stdout.flush()
    if not _qr_opened and sys.platform == "darwin" and sys.stdout.isatty():
        _qr_opened = True
        try:
            subprocess.Popen(["open", str(QR_FILE)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def _report_status(client: NewClient) -> None:
    """Pairing-/Verbindungsereignisse sichtbar machen, damit ein
    fehlgeschlagener Scan nicht stumm bleibt."""

    @client.event(PairStatusEv)
    def _(client: NewClient, ev: PairStatusEv) -> None:
        if ev.Error:
            print(f"Kopplung fehlgeschlagen: {ev.Error}", file=sys.stderr)
        else:
            print(f"Gekoppelt als {Jid2String(ev.ID)} ({ev.Platform}). Verbinde …")
        sys.stdout.flush()

    @client.event(ConnectFailureEv)
    def _(client: NewClient, ev: ConnectFailureEv) -> None:
        print(f"Verbindung abgelehnt: {ev.Reason} {ev.Message}", file=sys.stderr)

    @client.event(LoggedOutEv)
    def _(client: NewClient, ev: LoggedOutEv) -> None:
        print(f"Von WhatsApp ausgeloggt (Grund {ev.Reason}). Session-Datei löschen "
              f"und neu koppeln: rm '{SESSION}'", file=sys.stderr)

    @client.event(StreamErrorEv)
    def _(client: NewClient, ev: StreamErrorEv) -> None:
        print(f"Stream-Fehler: {ev.Code}", file=sys.stderr)

    @client.event(ClientOutdatedEv)
    def _(client: NewClient, ev: ClientOutdatedEv) -> None:
        print("WhatsApp meldet: Client veraltet. neonize aktualisieren: "
              "uv lock --upgrade-package neonize && uv sync", file=sys.stderr)

    @client.event(TemporaryBanEv)
    def _(client: NewClient, ev: TemporaryBanEv) -> None:
        print(f"Temporär gesperrt: Code {ev.Code}, bis {ev.Expire}", file=sys.stderr)


def _run(work: Callable[[NewClient], None], timeout: int,
         pair_phone: str | None = None) -> None:
    """Verbinden, `work` einmal nach dem Connect ausführen, wieder auflegen.

    `client.connect()` blockiert und kehrt nach `stop()` nicht zuverlässig
    zurück (vor allem, solange noch kein Gerät gekoppelt ist). Außerdem hängt
    neonize intern einen Nicht-Daemon-Thread auf, sodass der Interpreter beim
    Beenden ewig wartet. Deshalb läuft connect() in einem Thread, der
    Hauptthread wartet mit Zeitlimit, und ein Watchdog beendet den Prozess
    zur Not hart — ein hängender Cron-Job wäre schlimmer als ein unsauberer
    Exit.
    """
    client = NewClient(str(SESSION))
    finished = threading.Event()
    outcome: dict[str, BaseException | None] = {"error": None}
    pairing_started = threading.Event()

    _report_status(client)

    @client.event.qr
    def _(client: NewClient, data: bytes) -> None:
        if pair_phone is None:
            _show_qr(client, data)
            return
        # Alternative zum Scannen: 8-stelliger Code, den man in WhatsApp unter
        # "Stattdessen mit Telefonnummer verknüpfen" eintippt.
        if pairing_started.is_set():
            return
        pairing_started.set()

        def _request_code() -> None:
            try:
                code = client.PairPhone(pair_phone, show_push_notification=True)
                print(f"\nKopplungscode: {code}\n"
                      "WhatsApp → Verknüpfte Geräte → Gerät hinzufügen → "
                      "Stattdessen mit Telefonnummer verknüpfen\n")
                sys.stdout.flush()
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = WhatsAppError(f"Kopplungscode anfordern fehlgeschlagen: {exc}")
                finished.set()

        threading.Thread(target=_request_code, daemon=True).start()

    @client.event(ConnectedEv)
    def _(client: NewClient, _event: ConnectedEv) -> None:
        if finished.is_set():  # Reconnect nach getaner Arbeit ignorieren
            return
        try:
            work(client)
        except BaseException as exc:  # noqa: BLE001 - an den Aufrufer durchreichen
            outcome["error"] = exc
        finally:
            finished.set()

    def _connect() -> None:
        try:
            client.connect()
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = outcome["error"] or exc
            finished.set()

    thread = threading.Thread(target=_connect, name="wa-connect", daemon=True)
    thread.start()

    if not finished.wait(timeout):
        outcome["error"] = WhatsAppError(
            f"Keine Verbindung zu WhatsApp innerhalb von {timeout}s "
            "(QR nicht gescannt, kein Netz oder Session ungültig)."
        )

    try:
        client.stop()
    except Exception:  # noqa: BLE001 - beim Aufräumen egal
        pass
    thread.join(timeout=10)

    if thread.is_alive():
        # Go-Seite hängt noch. Dem Aufrufer Zeit lassen, seinen Kram zu Ende
        # zu bringen (State speichern, Fehler ausgeben), dann hart raus.
        _arm_watchdog(exit_code=1 if outcome["error"] else 0)

    if outcome["error"] is not None:
        raise outcome["error"]


WATCHDOG_GRACE = 15


def _arm_watchdog(exit_code: int) -> None:
    def _bail() -> None:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)

    timer = threading.Timer(WATCHDOG_GRACE, _bail)
    timer.daemon = True
    timer.start()


def _parse_jid(value: str) -> JID:
    """'1203630…@g.us' → neonize-JID-Objekt."""
    user, _, server = value.partition("@")
    if not user or not server:
        raise ValueError(f"Ungültige JID: {value!r} (erwartet 'user@g.us')")
    return build_jid(user, server)


def list_groups(pair_phone: str | None = None) -> None:
    """Einmalig aufrufen, um die JID der Zielgruppe herauszufinden.

    `pair_phone`: eigene Nummer im Format +49170…, dann wird statt des
    QR-Codes ein Kopplungscode ausgegeben.
    """

    def work(client: NewClient) -> None:
        groups = client.get_joined_groups()
        if not groups:
            print("Keine Gruppen gefunden.")
        for group in groups:
            print(f"{Jid2String(group.JID)}\t{group.GroupName.Name}")
        sys.stdout.flush()

    _run(work, timeout=PAIR_TIMEOUT, pair_phone=pair_phone)


def send(group_jid: str, text: str) -> None:
    """Eine Nachricht in die Gruppe schicken und sofort wieder auflegen."""
    target = _parse_jid(group_jid)

    def work(client: NewClient) -> None:
        client.send_message(target, text)

    _run(work, timeout=CONNECT_TIMEOUT)
