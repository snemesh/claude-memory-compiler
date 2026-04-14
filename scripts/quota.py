"""Subscription quota reader for Claude Code (Pro/Max plans).

Primary source: GET https://api.anthropic.com/api/oauth/usage with OAuth token
from Claude Code credentials. Fallback: aggregate ~/.claude/projects/**/*.jsonl
with model pricing table.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
_HTTP_TIMEOUT_S = 10
_KEYCHAIN_SERVICE = "Claude Code-credentials"

_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def _read_macos_keychain() -> str | None:
    """On macOS, Claude Code stores the OAuth blob in the Keychain under
    service='Claude Code-credentials'. Returns None if not on macOS, if
    security(1) fails, or if the blob is malformed.
    """
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None
    return data.get("claudeAiOauth", {}).get("accessToken")


def find_oauth_token() -> str | None:
    """Locate the Claude Code OAuth access token.

    Order: credentials file (~/.claude/.credentials.json), then macOS keychain.
    Returns None if no source yields a token.
    """
    if _CREDENTIALS_PATH.exists():
        try:
            data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
            token = data.get("claudeAiOauth", {}).get("accessToken")
            if token:
                return token
        except (json.JSONDecodeError, OSError):
            pass
    return _read_macos_keychain()


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
class BudgetThresholds:
    """Configurable stop-points for sync-loop.

    Values are percentages 0-100. Defaults leave conservative headroom
    for interactive use outside the sync process.
    """
    five_hour_stop_pct: float = 85.0
    seven_day_stop_pct: float = 90.0


def should_pause(snap: QuotaSnapshot, thresholds: BudgetThresholds) -> bool:
    """True if sync-loop must stop before next chunk."""
    if snap.five_hour_used_pct >= thresholds.five_hour_stop_pct:
        return True
    if snap.seven_day_used_pct >= thresholds.seven_day_stop_pct:
        return True
    return False


def sleep_seconds_until_reset(snap: QuotaSnapshot, safety_margin_s: int = 30) -> int:
    """Seconds from snapshot time until the nearest blocking window resets.

    Picks whichever of five_hour / seven_day is currently over its threshold;
    if both, returns the earlier reset. Adds safety_margin_s so we wake up
    slightly after the reset, not on the boundary.
    """
    candidates = [snap.five_hour_resets_at, snap.seven_day_resets_at]
    earliest = min(candidates)
    delta = earliest - snap.fetched_at
    return int(delta.total_seconds()) + safety_margin_s


_DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def get_quota(
    budget_5h_usd: float = 2500.0,
    budget_7d_usd: float = 20000.0,
    projects_dir: Path | None = None,
) -> QuotaSnapshot | None:
    """High-level API: return best-effort quota snapshot.

    Order:
      1. oauth/usage endpoint (currently returns 401 — Anthropic upstream)
      2. jsonl-based estimate from ~/.claude/projects/**/*.jsonl

    Defaults are sized for Max-20x ($200/mo) peak usage: one active work
    session typically registers ~$2000 of API-equivalent spend per 5h
    window via jsonl (subscription ROI is ~10-100x list price). Tune
    by running `quota.py` at end-of-day and comparing against /usage
    in the Claude Code TUI. Lower values = more conservative throttling.
    """
    token = find_oauth_token()
    if token:
        snap = fetch_oauth_quota(token)
        if snap is not None:
            return snap

    # Fallback: local jsonl parsing
    from jsonl_quota import estimate_quota_from_jsonl
    proj_dir = projects_dir or _DEFAULT_PROJECTS_DIR
    if not proj_dir.exists():
        return None
    return estimate_quota_from_jsonl(
        projects_dir=proj_dir,
        budget_5h_usd=budget_5h_usd,
        budget_7d_usd=budget_7d_usd,
    )


def _main() -> int:
    """CLI entrypoint: `uv run python scripts/quota.py`"""
    snap = get_quota()
    if snap is None:
        print("quota: unavailable (no OAuth token or endpoint error)")
        return 1
    print(f"source: {snap.source}")
    print(f"fetched_at: {snap.fetched_at.isoformat()}")
    print(f"5h: {snap.five_hour_used_pct:.1f}% used "
          f"(resets {snap.five_hour_resets_at.isoformat()})")
    print(f"7d: {snap.seven_day_used_pct:.1f}% used "
          f"(resets {snap.seven_day_resets_at.isoformat()})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())


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
