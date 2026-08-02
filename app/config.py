"""Konfiguration des Ida-Reminder MCP Servers, komplett über Umgebungsvariablen."""

from __future__ import annotations

import os
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
    max_slots: int
    timezone: str
    vorlauf_minuten: int
    poll_intervall_sekunden: int
    state_path: str

    mcp_auth_token: str
    mcp_host: str
    mcp_port: int


def load_settings() -> Settings:
    try:
        max_slots = int(_optional("MAX_SLOTS", "10"))
        if not (1 <= max_slots <= 50):
            raise ConfigError("MAX_SLOTS muss zwischen 1 und 50 liegen.")

        slots: dict[int, SlotZugang] = {}
        for platz in range(1, max_slots + 1):
            routine_id = os.environ.get(f"REMINDER_SLOT_{platz}_ROUTINE_ID", "").strip()
            api_key = os.environ.get(f"REMINDER_SLOT_{platz}_API_KEY", "").strip()
            if routine_id and api_key:
                slots[platz] = SlotZugang(routine_id=routine_id, api_key=api_key)
            elif routine_id or api_key:
                raise ConfigError(
                    f"Platz {platz}: nur REMINDER_SLOT_{platz}_ROUTINE_ID oder nur "
                    f"REMINDER_SLOT_{platz}_API_KEY gesetzt -- es werden immer beide "
                    "gebraucht (oder keins von beiden, dann bleibt der Platz einfach "
                    "ungenutzt)."
                )
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
            max_slots=max_slots,
            timezone=tz_name,
            vorlauf_minuten=vorlauf_minuten,
            poll_intervall_sekunden=poll_intervall_sekunden,
            state_path=_optional("STATE_PATH", "/data/state.json"),
            mcp_auth_token=mcp_auth_token,
            mcp_host=_optional("MCP_HOST", "0.0.0.0"),
            mcp_port=int(_optional("MCP_PORT", "8030")),
        )
    except ConfigError as exc:
        print(f"[Ida-Reminder] Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    return settings
