"""Subscription quota reader for Claude Code (Pro/Max plans).

Primary source: GET https://api.anthropic.com/api/oauth/usage with OAuth token
from Claude Code credentials. Fallback: aggregate ~/.claude/projects/**/*.jsonl
with model pricing table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def find_oauth_token() -> str | None:
    """Locate the Claude Code OAuth access token.

    Searches ~/.claude/.credentials.json for claudeAiOauth.accessToken.
    Returns None if the file is absent or the token is missing.
    """
    if not _CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("claudeAiOauth", {}).get("accessToken")


@dataclass(frozen=True)
class QuotaSnapshot:
    """Point-in-time snapshot of subscription usage."""
    five_hour_used_pct: float
    five_hour_resets_at: datetime
    seven_day_used_pct: float
    seven_day_resets_at: datetime
    fetched_at: datetime
    source: str  # "oauth" | "jsonl-fallback"

    @property
    def five_hour_remaining_pct(self) -> float:
        return 100.0 - self.five_hour_used_pct

    @property
    def seven_day_remaining_pct(self) -> float:
        return 100.0 - self.seven_day_used_pct
