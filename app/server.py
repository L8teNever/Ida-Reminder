"""Ida-Reminder MCP Server.

Erlaubt Claude, sich selbst zeitversetzt an eine Aufgabe zu erinnern: statt
eines eigenen Cron-Jobs pro Erinnerung gibt es beliebig viele 'Plaetze',
jeder mit einem eigenen claude.ai-Routine-Trigger (routine_id + API-Key aus
der .env) -- wie viele es gibt, erkennt der Server automatisch daran, wie
viele REMINDER_SLOT_<N>_ROUTINE_ID/_API_KEY-Paare tatsaechlich gesetzt sind,
kein Limit muss irgendwo eingestellt werden. erinnerung_erstellen belegt automatisch den naechsten freien
Platz; ein Hintergrund-Thread (app/scheduler.py) prueft periodisch, ob ein
Platz faellig ist (Zielzeitpunkt minus Vorlaufzeit erreicht), und loest dann
die zu diesem Platz gehoerende Routine aus -- die Aufgabe bekommt sie direkt
in der Ausloese-Nachricht mitgeschickt, kein Nachlesen noetig. Der Server
raeumt NICHTS automatisch selbst auf (das wuerde gegen die gerade erst
ausgeloeste Routine racen): die ausgeloeste Routine entscheidet nach dem
Ausfuehren selbst, ob sie sich mit erinnerung_leeren aufraeumt (fertig) oder
mit erinnerung_erstellen(..., platz=<eigene Nummer>) fuer den naechsten
Termin neu einplant -- so bleibt derselbe Routine-Trigger fuer beliebig
viele, inhaltlich ganz unterschiedliche und auch wiederkehrende Erinnerungen
wiederverwendbar.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from app.auth import BearerAuthMiddleware
from app.config import load_settings
from app.scheduler import starten as scheduler_starten
from app.state import (
    SlotEintrag,
    alle_plaetze_lesen,
    naechsten_freien_platz_finden,
    platz_belegen,
    platz_freigeben,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ida-reminder")

settings = load_settings()

mcp = FastMCP(
    "Ida-Reminder",
    instructions=(
        "Werkzeuge, um sich selbst zeitversetzt an eine Aufgabe zu erinnern. "
        "erinnerung_erstellen(zeitpunkt, aufgabe) reicht fuer den Normalfall -- "
        "sucht sich automatisch einen freien Platz, alles Weitere (Timing, "
        "Ausloesen inkl. der Aufgabe direkt in der Nachricht) macht der "
        "Server selbst im Hintergrund. WICHTIG fuer die ausgeloeste Routine "
        "selbst: der Server raeumt nach dem Ausloesen NICHTS automatisch auf "
        "-- du musst nach dem Ausfuehren der Aufgabe selbst entscheiden und "
        "aktiv werden: entweder erinnerung_leeren(platz=N) aufrufen (fertig, "
        "einmalige Aufgabe) oder erinnerung_erstellen(zeitpunkt=<naechster "
        "Termin>, aufgabe=..., platz=N) mit DERSELBEN Platznummer (fuer "
        "wiederkehrende Aufgaben, um sich selbst neu einzuplanen). "
        "erinnerungen_liste zeigt den Belegungsstatus aller Plaetze (mit "
        "platz=N nur einen einzelnen). erinnerung_leeren hat bewusst KEINEN "
        "Bestaetigungs-Zwang, weil eine automatisch ausgeloeste Routine sich "
        "damit selbststaendig aufraeumen koennen muss, ohne dass jemand im "
        "Chat sitzt, der eine Rueckfrage beantworten koennte. "
        "aktuelle_uhrzeit liefert die aktuelle Zeit in der Server-Zeitzone -- "
        "damit relative Angaben ('in 10 Minuten', 'heute Abend') in einen "
        "absoluten zeitpunkt fuer erinnerung_erstellen umgerechnet werden "
        "koennen, ohne dafuer extra einen Shell-Befehl auf einem anderen "
        "Server auszufuehren."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
)


def _zeitpunkt_parsen(zeitpunkt: str) -> datetime:
    text = zeitpunkt.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        wert = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Ungueltiger zeitpunkt {zeitpunkt!r} -- erwartet ISO 8601, z.B. "
            "'2026-08-01T15:00:00' (Server-Zeitzone) oder mit Offset "
            "'2026-08-01T15:00:00+02:00'."
        ) from exc
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=ZoneInfo(settings.timezone))
    return wert


@mcp.tool()
def erinnerung_erstellen(zeitpunkt: str, aufgabe: str, einmalig: bool = True, platz: int = 0) -> dict:
    """Plant eine Erinnerung: loest zum angegebenen Zeitpunkt (minus Vorlaufzeit,
    Standard 5 Minuten, siehe erinnerungen_liste fuer die genaue Konfiguration)
    automatisch eine Claude Routine aus, die die Aufgabe direkt in der
    Ausloese-Nachricht mitgeschickt bekommt (kein Nachlesen noetig). Sucht
    sich standardmaessig selbststaendig den naechsten freien von mehreren
    konfigurierten Plaetzen -- kein weiterer Tool-Aufruf noetig.

    zeitpunkt: ISO 8601 Datum+Uhrzeit, z.B. '2026-08-01T15:00:00'. Ohne
        Zeitzonen-Offset wird die konfigurierte Server-Zeitzone angenommen.
        Muss in der Zukunft liegen.
    aufgabe: Freitext, wird der ausgeloesten Routine 1:1 in der Ausloese-
        Nachricht mitgegeben -- die Routine bekommt sonst keinen weiteren
        Chat-Kontext mit, also entsprechend selbststaendig verstaendlich
        formulieren.
    einmalig: True (Standard) = nur ein Hinweis fuer die ausgeloeste Routine,
        wie diese Erinnerung gedacht war. Entscheidet NICHT automatisch,
        was passiert -- das macht die Routine nach dem Ausfuehren selbst:
        entweder erinnerung_leeren (fertig) oder erinnerung_erstellen mit
        gleichem platz und neuem zeitpunkt (naechster Termin).
    platz: 0 (Standard) = naechsten freien Platz automatisch waehlen. Sonst
        genau diesen Platz belegen/ueberschreiben, egal ob er gerade frei,
        belegt oder schon ausgeloest ist -- so kann eine gerade ausgeloeste
        Routine sich selbst mit ihrer eigenen Platznummer fuer den naechsten
        Termin neu einplanen.
    """
    ziel_dt = _zeitpunkt_parsen(zeitpunkt)

    jetzt = datetime.now(timezone.utc)
    if ziel_dt <= jetzt:
        raise ValueError(f"zeitpunkt {zeitpunkt!r} liegt in der Vergangenheit.")

    ausloese_dt = ziel_dt - timedelta(minutes=settings.vorlauf_minuten)
    if ausloese_dt <= jetzt:
        ausloese_dt = jetzt

    if platz == 0:
        gewaehlter_platz = naechsten_freien_platz_finden(settings)
        if gewaehlter_platz is None:
            raise ValueError(
                f"Alle {len(settings.slots)} konfigurierten Plaetze sind aktuell belegt -- "
                "erinnerungen_liste zeigt den Belegungsstatus, ggf. erst erinnerung_leeren "
                "fuer einen nicht mehr benoetigten Platz aufrufen."
            )
    elif platz in settings.slots:
        gewaehlter_platz = platz
    else:
        raise ValueError(f"Platz {platz} existiert nicht (konfigurierte Plaetze: {sorted(settings.slots)}).")

    eintrag = SlotEintrag(
        aufgabe=aufgabe,
        zielzeitpunkt=ziel_dt.isoformat(),
        ausloesezeitpunkt=ausloese_dt.isoformat(),
        einmalig=einmalig,
        erstellt_am=jetzt.isoformat(),
    )
    platz_belegen(settings, gewaehlter_platz, eintrag)

    return {
        "platz": gewaehlter_platz,
        "zielzeitpunkt": eintrag.zielzeitpunkt,
        "ausloesezeitpunkt": eintrag.ausloesezeitpunkt,
        "einmalig": einmalig,
        "hinweis": f"Erinnerung auf Platz {gewaehlter_platz} gespeichert, wird um {eintrag.ausloesezeitpunkt} automatisch ausgeloest.",
    }


@mcp.tool()
def erinnerungen_liste(platz: int = 0) -> list[dict] | dict:
    """Zeigt Erinnerungs-Plaetze: Belegungsstatus, Aufgabe (falls belegt),
    Ziel-/Ausloesezeitpunkt, einmalig-Flag und ob schon ausgeloest.

    platz: 0 (Standard) = alle konfigurierten Plaetze als Liste. Sonst nur
        der eine angegebene Platz als einzelnes Objekt -- das ist der Weg,
        wie eine gerade ausgeloeste Routine ihre eigene Aufgabe nachliest.
    """
    alle = alle_plaetze_lesen(settings)
    if platz == 0:
        return alle
    treffer = next((p for p in alle if p["platz"] == platz), None)
    if treffer is None:
        raise ValueError(f"Platz {platz} existiert nicht (konfigurierte Plaetze: {sorted(settings.slots)}).")
    return treffer


_WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


@mcp.tool()
def aktuelle_uhrzeit() -> dict:
    """Gibt die aktuelle Uhrzeit zurueck (Server-Zeitzone + UTC). Nuetzlich um
    z.B. eine relative Angabe ('in 10 Minuten', 'heute Abend') in einen
    absoluten zeitpunkt fuer erinnerung_erstellen umzurechnen, ohne dafuer
    extra einen Shell-Befehl auf einem anderen Server ausfuehren zu muessen.
    """
    jetzt_utc = datetime.now(timezone.utc)
    jetzt_lokal = jetzt_utc.astimezone(ZoneInfo(settings.timezone))
    return {
        "iso_lokal": jetzt_lokal.isoformat(),
        "iso_utc": jetzt_utc.isoformat(),
        "zeitzone": settings.timezone,
        "lesbar": f"{_WOCHENTAGE[jetzt_lokal.weekday()]}, {jetzt_lokal.strftime('%d.%m.%Y %H:%M:%S')}",
    }


@mcp.tool()
def erinnerung_leeren(platz: int) -> dict:
    """Gibt einen Platz wieder frei (loescht die dort gespeicherte Aufgabe).
    Funktioniert sowohl fuer noch nicht ausgeloeste Plaetze (= Erinnerung vor
    dem Ausloesen abbrechen) als auch fuer bereits ausgeloeste (= Aufraeumen
    nach einer einmaligen Aufgabe). Bewusst KEIN bestaetigt-Zwang -- muss auch
    von einer automatisch ausgeloesten Routine ohne Rueckfrage aufrufbar sein.
    """
    entfernt = platz_freigeben(settings, platz)
    if not entfernt:
        raise ValueError(f"Platz {platz} war nicht belegt.")
    return {"platz": platz, "freigegeben": True}


async def healthz(request):
    return JSONResponse({"status": "ok"})


def build_app():
    app = mcp.streamable_http_app()
    app.add_route("/healthz", healthz, methods=["GET"])
    app.add_middleware(BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app


def main() -> None:
    app = build_app()
    scheduler_starten(settings)
    log.info(
        "Ida-Reminder MCP Server startet auf %s:%s (Endpunkt: /mcp, Health: /healthz, "
        "%s konfigurierte Plaetze, Zeitzone %s)",
        settings.mcp_host, settings.mcp_port, len(settings.slots), settings.timezone,
    )
    # access_log=False: uvicorn wuerde sonst jede Request-Zeile inkl. vollem
    # Pfad loggen -- und damit ein per ?token= mitgeschicktes MCP_AUTH_TOKEN
    # im Klartext in die Docker-Logs schreiben.
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
