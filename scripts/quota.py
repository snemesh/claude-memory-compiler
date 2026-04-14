"""Subscription quota reader for Claude Code (Pro/Max plans).

Primary source: GET https://api.anthropic.com/api/oauth/usage with OAuth token
from Claude Code credentials. Fallback: aggregate ~/.claude/projects/**/*.jsonl
with model pricing table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
_HTTP_TIMEOUT_S = 10

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


def fetch_oauth_quota(token: str) -> QuotaSnapshot | None:
    """Call the oauth/usage endpoint; return snapshot or None on any failure.

    This endpoint is undocumented but used by Claude Code's /usage command.
    Returns None on HTTP errors, network errors, or malformed response —
    callers fall back to jsonl-based estimation.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "claude-memory-compiler/0.1",
    }
    try:
        resp = requests.get(_USAGE_ENDPOINT, headers=headers, timeout=_HTTP_TIMEOUT_S)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
        five = data["five_hour"]
        seven = data["seven_day"]
        now = datetime.now(tz=timezone.utc)
        return QuotaSnapshot(
            five_hour_used_pct=float(five["used_percentage"]),
            five_hour_resets_at=datetime.fromtimestamp(five["resets_at"], tz=timezone.utc),
            seven_day_used_pct=float(seven["used_percentage"]),
            seven_day_resets_at=datetime.fromtimestamp(seven["resets_at"], tz=timezone.utc),
            fetched_at=now,
            source="oauth",
        )
    except (KeyError, ValueError, TypeError):
        return None


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
