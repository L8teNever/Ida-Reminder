"""Persistenter Zustand der Erinnerungs-'Plaetze' (Platznummer -> Aufgabe),
als einfache JSON-Datei -- ueberlebt Container-Neustarts trotz read-only
Rootfs, weil /data als eigenes beschreibbares Volume gemountet ist (siehe
docker-compose.yml). Ein threading.Lock schuetzt vor gleichzeitigem
Lesen/Schreiben durch MCP-Tool-Aufrufe und den Scheduler-Hintergrund-Thread.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime

from app.config import Settings

_lock = threading.Lock()


@dataclass
class SlotEintrag:
    aufgabe: str
    zielzeitpunkt: str  # ISO 8601, mit Zeitzonen-Offset
    ausloesezeitpunkt: str  # ISO 8601, mit Zeitzonen-Offset (= zielzeitpunkt - Vorlauf)
    einmalig: bool
    erstellt_am: str  # ISO 8601, mit Zeitzonen-Offset
    ausgeloest: bool = False


def _lesen(pfad: str) -> dict[str, dict]:
    if not os.path.exists(pfad):
        return {}
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _schreiben(pfad: str, daten: dict[str, dict]) -> None:
    verzeichnis = os.path.dirname(pfad) or "."
    os.makedirs(verzeichnis, exist_ok=True)
    fd, tmp_pfad = tempfile.mkstemp(dir=verzeichnis, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
        os.replace(tmp_pfad, pfad)
    except BaseException:
        if os.path.exists(tmp_pfad):
            os.remove(tmp_pfad)
        raise


def alle_plaetze_lesen(settings: Settings) -> list[dict]:
    with _lock:
        daten = _lesen(settings.state_path)
    ergebnis = []
    for platz in range(1, settings.max_slots + 1):
        eintrag = daten.get(str(platz))
        info = {
            "platz": platz,
            "konfiguriert": platz in settings.slots,
            "belegt": eintrag is not None,
        }
        if eintrag is not None:
            info.update(eintrag)
        ergebnis.append(info)
    return ergebnis


def naechsten_freien_platz_finden(settings: Settings) -> int | None:
    with _lock:
        daten = _lesen(settings.state_path)
    for platz in sorted(settings.slots):
        if str(platz) not in daten:
            return platz
    return None


def platz_belegen(settings: Settings, platz: int, eintrag: SlotEintrag) -> None:
    with _lock:
        daten = _lesen(settings.state_path)
        daten[str(platz)] = asdict(eintrag)
        _schreiben(settings.state_path, daten)


def platz_freigeben(settings: Settings, platz: int) -> bool:
    with _lock:
        daten = _lesen(settings.state_path)
        if str(platz) not in daten:
            return False
        del daten[str(platz)]
        _schreiben(settings.state_path, daten)
        return True


def platz_als_ausgeloest_markieren(settings: Settings, platz: int) -> None:
    with _lock:
        daten = _lesen(settings.state_path)
        if str(platz) in daten:
            daten[str(platz)]["ausgeloest"] = True
            _schreiben(settings.state_path, daten)


def faellige_plaetze_finden(settings: Settings, jetzt: datetime) -> list[tuple[int, dict]]:
    """Rein lesend -- markiert nichts. Der Scheduler markiert einen Platz erst
    nach ERFOLGREICHEM Ausloesen als ausgeloest, damit ein fehlgeschlagener
    Versuch (z.B. Netzwerkfehler) beim naechsten Durchlauf automatisch erneut
    versucht wird, statt die Erinnerung stillschweigend zu verlieren."""
    with _lock:
        daten = _lesen(settings.state_path)
    faellig = []
    for platz_str, eintrag in daten.items():
        if eintrag.get("ausgeloest"):
            continue
        try:
            ausloesezeitpunkt = datetime.fromisoformat(eintrag["ausloesezeitpunkt"])
        except (KeyError, ValueError):
            continue
        if ausloesezeitpunkt <= jetzt:
            faellig.append((int(platz_str), eintrag))
    return faellig
