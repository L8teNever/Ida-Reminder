"""Hintergrund-Thread, der faellige Erinnerungen erkennt und die zugehoerige
Claude Routine ausloest. Laeuft unabhaengig vom ASGI-Request/Response-Zyklus
(eigener Thread, eigene Schleife) -- Ausloesungen muessen auch ganz ohne
eingehenden MCP-Request passieren, rein zeitgesteuert.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.config import Settings
from app.routine_client import routine_ausloesen
from app.state import faellige_plaetze_finden, platz_als_ausgeloest_markieren

log = logging.getLogger("ida-reminder.scheduler")


def _nachricht_bauen(platz: int, eintrag: dict) -> str:
    einmalig = eintrag.get("einmalig", True)
    hinweis = "einmalig" if einmalig else "wiederkehrend"
    return (
        f"[Ida-Reminder] Erinnerung auf Platz {platz} ist jetzt faellig "
        f"(urspruenglich als {hinweis} angelegt -- nur ein Hinweis, du "
        "entscheidest selbst was als naechstes passiert):\n\n"
        f"{eintrag.get('aufgabe', '')}\n\n"
        "Fuehre diese Aufgabe jetzt aus. Entscheide danach selbst: War es "
        f"eine einmalige Sache, rufe erinnerung_leeren(platz={platz}) auf, "
        "um den Platz wieder freizugeben. Soll die Erinnerung wiederkehren, "
        "rufe stattdessen erinnerung_erstellen(zeitpunkt=<naechster Termin>, "
        f"aufgabe=..., einmalig=..., platz={platz}) auf, um genau diesen "
        "Platz fuer den naechsten Termin neu zu belegen."
    )


def _durchlauf(settings: Settings) -> None:
    jetzt = datetime.now(timezone.utc)
    for platz, eintrag in faellige_plaetze_finden(settings, jetzt):
        zugang = settings.slots.get(platz)
        if zugang is None:
            log.warning("Faelliger Platz %s hat keine konfigurierten Zugangsdaten (mehr), ueberspringe", platz)
            continue

        erfolg = routine_ausloesen(zugang.routine_id, zugang.api_key, _nachricht_bauen(platz, eintrag))
        if not erfolg:
            log.error("Ausloesen der Routine fuer Platz %s fehlgeschlagen, wird beim naechsten Durchlauf erneut versucht", platz)
            continue

        log.info("Routine fuer Platz %s ausgeloest", platz)
        # Der Platz wird NICHT hier automatisch geleert/wiederverwendet --
        # das wuerde gegen die Routine racen, die die Aufgabe gerade erst
        # ausfuehrt. Nur als ausgeloest markiert, damit er nicht bei jedem
        # weiteren Poll-Durchlauf erneut feuert. Ob/wann der Platz wieder
        # frei wird, entscheidet die Routine selbst (erinnerung_leeren bzw.
        # erinnerung_erstellen mit demselben platz zum Neu-Einplanen).
        platz_als_ausgeloest_markieren(settings, platz)


def _schleife(settings: Settings, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            _durchlauf(settings)
        except Exception:
            log.exception("Fehler im Reminder-Scheduler-Durchlauf, versuche es beim naechsten weiter")
        stop_event.wait(settings.poll_intervall_sekunden)


def starten(settings: Settings) -> threading.Event:
    stop_event = threading.Event()
    thread = threading.Thread(target=_schleife, args=(settings, stop_event), daemon=True, name="ida-reminder-scheduler")
    thread.start()
    log.info("Scheduler gestartet (Poll-Intervall %ss, %s konfigurierte Plaetze)", settings.poll_intervall_sekunden, len(settings.slots))
    return stop_event
