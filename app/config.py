"""Konfiguration des Ida-Reminder MCP Servers, komplett über Umgebungsvariablen."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Umgebungsvariable {name} fehlt oder ist leer.")
    return value


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class SlotZugang:
    routine_id: str
    api_key: str


@dataclass(frozen=True)
class Settings:
    slots: dict[int, SlotZugang]
    timezone: str
    vorlauf_minuten: int
    poll_intervall_sekunden: int
    state_path: str

    mcp_auth_token: str
    mcp_host: str
    mcp_port: int


_SLOT_VAR_PATTERN = re.compile(r"^REMINDER_SLOT_(\d+)_(ROUTINE_ID|API_KEY)$")


def _slots_aus_umgebung_lesen() -> dict[int, SlotZugang]:
    """Scannt die Umgebung selbst nach REMINDER_SLOT_<N>_ROUTINE_ID/_API_KEY --
    kein fester Deckel: wie viele Plaetze es gibt, ergibt sich automatisch
    daraus, wie viele Paare tatsaechlich in der .env eingetragen sind (egal ob
    lueckenlos durchnummeriert oder nicht)."""
    gefunden: dict[int, dict[str, str]] = {}
    for name, value in os.environ.items():
        match = _SLOT_VAR_PATTERN.match(name)
        if not match:
            continue
        wert = value.strip()
        if not wert:
            continue
        nummer = int(match.group(1))
        gefunden.setdefault(nummer, {})[match.group(2)] = wert

    slots: dict[int, SlotZugang] = {}
    for nummer, werte in sorted(gefunden.items()):
        routine_id = werte.get("ROUTINE_ID")
        api_key = werte.get("API_KEY")
        if routine_id and api_key:
            slots[nummer] = SlotZugang(routine_id=routine_id, api_key=api_key)
        else:
            fehlend = f"REMINDER_SLOT_{nummer}_API_KEY" if routine_id else f"REMINDER_SLOT_{nummer}_ROUTINE_ID"
            raise ConfigError(
                f"Platz {nummer}: {fehlend} fehlt -- es werden immer beide Werte "
                "gebraucht (ROUTINE_ID und API_KEY)."
            )
    return slots


def load_settings() -> Settings:
    try:
        slots = _slots_aus_umgebung_lesen()
        if not slots:
            raise ConfigError(
                "Kein einziger Platz konfiguriert -- mindestens ein Paar "
                "REMINDER_SLOT_1_ROUTINE_ID / REMINDER_SLOT_1_API_KEY in der .env setzen."
            )

        tz_name = _optional("TIMEZONE", "Europe/Berlin")
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"TIMEZONE={tz_name!r} ist keine gueltige IANA-Zeitzone (z.B. 'Europe/Berlin').") from exc

        vorlauf_minuten = int(_optional("VORLAUF_MINUTEN", "5"))
        if vorlauf_minuten < 0:
            raise ConfigError("VORLAUF_MINUTEN darf nicht negativ sein.")

        poll_intervall_sekunden = int(_optional("POLL_INTERVALL_SEKUNDEN", "20"))
        if poll_intervall_sekunden < 1:
            raise ConfigError("POLL_INTERVALL_SEKUNDEN muss mindestens 1 sein.")

        mcp_auth_token = _require("MCP_AUTH_TOKEN")
        if len(mcp_auth_token) < 16:
            raise ConfigError(
                "MCP_AUTH_TOKEN ist zu kurz (mind. 16 Zeichen). "
                "Erzeuge z.B. mit: openssl rand -hex 32"
            )

        settings = Settings(
            slots=slots,
            timezone=tz_name,
            vorlauf_minuten=vorlauf_minuten,
            poll_intervall_sekunden=poll_intervall_sekunden,
            state_path=_optional("STATE_PATH", "/data/state.json"),
            mcp_auth_token=mcp_auth_token,
            mcp_host=_optional("MCP_HOST", "0.0.0.0"),
            mcp_port=int(_optional("MCP_PORT", "8031")),
        )
    except ConfigError as exc:
        print(f"[Ida-Reminder] Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    return settings
