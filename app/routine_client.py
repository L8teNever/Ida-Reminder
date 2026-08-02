"""Loest eine claude.ai Routine ueber die offizielle Trigger-API aus.

Gleicher Mechanismus wie bei Ida-Telegram (app/telegram_poller.py) und dem
Beeper-Proxy: pro Routine ein eigener routine_id + API-Key, generiert beim
Anlegen eines API-Triggers in den Routine-Einstellungen auf claude.ai.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("ida-reminder.routine")

# https://platform.claude.com/docs/en/api/claude-code/routines-fire
_ROUTINE_FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"
_ROUTINE_BETA_HEADER = "experimental-cc-routine-2026-04-01"
_ANTHROPIC_VERSION = "2023-06-01"
_ROUTINE_TEXT_MAX_LENGTH = 65536


def routine_ausloesen(routine_id: str, api_key: str, text: str, timeout_sekunden: int = 30) -> bool:
    url = _ROUTINE_FIRE_URL.format(routine_id=routine_id)
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "anthropic-beta": _ROUTINE_BETA_HEADER,
                "anthropic-version": _ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            json={"text": text[:_ROUTINE_TEXT_MAX_LENGTH]},
            timeout=timeout_sekunden,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        log.exception("Fehler beim Ausloesen der Routine %s", routine_id)
        return False
