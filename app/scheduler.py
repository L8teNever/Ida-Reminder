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
from app.state import (
    faellige_plaetze_finden,
    platz_als_ausgeloest_markieren,
    platz_freigeben,
)

log = logging.getLogger("ida-reminder.scheduler")


def _nachricht_bauen(platz: int) -> str:
    return (
        f"[Ida-Reminder] Erinnerung auf Platz {platz} ist jetzt faellig. Rufe im "
        f"Ida-Reminder MCP-Server 'erinnerungen_liste' mit platz={platz} auf, um "
        "die dort hinterlegte Aufgabe zu lesen, und fuehre sie aus. Steht dort "
        f"einmalig=true, rufe danach 'erinnerung_leeren' mit platz={platz} auf, um "
        "den Platz wieder freizugeben -- bei einmalig=false den Platz einfach "
        "belegt lassen."
    )


def _durchlauf(settings: Settings) -> None:
    jetzt = datetime.now(timezone.utc)
    for platz, eintrag in faellige_plaetze_finden(settings, jetzt):
        zugang = settings.slots.get(platz)
        if zugang is None:
            log.warning("Faelliger Platz %s hat keine konfigurierten Zugangsdaten (mehr), ueberspringe", platz)
            continue

        erfolg = routine_ausloesen(zugang.routine_id, zugang.api_key, _nachricht_bauen(platz))
        if not erfolg:
            log.error("Ausloesen der Routine fuer Platz %s fehlgeschlagen, wird beim naechsten Durchlauf erneut versucht", platz)
            continue

        log.info("Routine fuer Platz %s ausgeloest", platz)
        if eintrag.get("einmalig", True):
            platz_freigeben(settings, platz)
        else:
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
