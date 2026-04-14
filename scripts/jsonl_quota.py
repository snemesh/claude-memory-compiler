"""Subscription-quota estimate from local JSONL session logs.

Until/unless Anthropic exposes a public subscription-usage endpoint for
Claude Code Pro/Max plans, we derive used_percentage from the jsonl logs
in ~/.claude/projects/**/*.jsonl: sum tokens in the relevant rolling
window, multiply by model pricing, divide by user-configured budget.

This is an estimate — actual plan windows may overlap or throttle
differently at peak times (per Anthropic's March 2026 bug post).
Good enough for "don't burn the whole quota overnight" decisions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quota import QuotaSnapshot


@dataclass(frozen=True)
class ModelPricing:
    """Per-MTok prices in USD. 5m/1h are cache-write tiers."""
    input_per_mtok: float
    cache_read_per_mtok: float
    cache_write_5m_per_mtok: float  # 1.25x input
    cache_write_1h_per_mtok: float  # 2x input
    output_per_mtok: float


# Anthropic pricing as of Apr 2026 (public list prices).
_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-6": ModelPricing(
        input_per_mtok=15.0,
        cache_read_per_mtok=1.50,
        cache_write_5m_per_mtok=18.75,
        cache_write_1h_per_mtok=30.0,
        output_per_mtok=75.0,
    ),
    "claude-sonnet-4-6": ModelPricing(
        input_per_mtok=3.0,
        cache_read_per_mtok=0.30,
        cache_write_5m_per_mtok=3.75,
        cache_write_1h_per_mtok=6.0,
        output_per_mtok=15.0,
    ),
    "claude-haiku-4-5": ModelPricing(
        input_per_mtok=1.0,
        cache_read_per_mtok=0.08,
        cache_write_5m_per_mtok=1.00,
        cache_write_1h_per_mtok=1.60,
        output_per_mtok=5.0,
    ),
}

_FALLBACK_PRICING_KEY = "claude-sonnet-4-6"


def _lookup_pricing(model: str) -> ModelPricing:
    """Resolve model id → pricing with sonnet fallback for unknown models.

    Matches prefixes loosely: 'claude-sonnet-4-6[1m]' falls back to sonnet
    too (rate-limits note: 1M beta has same list price).
    """
    if model in _PRICING:
        return _PRICING[model]
    for key, pricing in _PRICING.items():
        if model.startswith(key):
            return pricing
    return _PRICING[_FALLBACK_PRICING_KEY]


def compute_turn_cost(model: str, usage: dict[str, Any]) -> float:
    """Cost in USD for a single API turn, given usage dict from jsonl."""
    p = _lookup_pricing(model)

    input_t = usage.get("input_tokens", 0) or 0
    output_t = usage.get("output_tokens", 0) or 0
    cache_read_t = usage.get("cache_read_input_tokens", 0) or 0
    cache_create_t = usage.get("cache_creation_input_tokens", 0) or 0

    # Split cache_write into 5m vs 1h tiers if breakdown is present.
    cache_detail = usage.get("cache_creation") or {}
    cache_5m = cache_detail.get("ephemeral_5m_input_tokens", 0) or 0
    cache_1h = cache_detail.get("ephemeral_1h_input_tokens", 0) or 0

    # If the breakdown is absent or zero, treat all cache_create as 5m default.
    if cache_5m + cache_1h == 0 and cache_create_t > 0:
        cache_5m = cache_create_t

    cost = (
        input_t * p.input_per_mtok / 1e6
        + output_t * p.output_per_mtok / 1e6
        + cache_read_t * p.cache_read_per_mtok / 1e6
        + cache_5m * p.cache_write_5m_per_mtok / 1e6
        + cache_1h * p.cache_write_1h_per_mtok / 1e6
    )
    return cost


def parse_usage_line(line: str) -> tuple[str, dict[str, Any], datetime] | None:
    """Extract (model, usage, timestamp) from one jsonl line. None if irrelevant."""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    msg = data.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    model = msg.get("model")
    if not isinstance(model, str):
        return None
    ts_raw = data.get("timestamp")
    if not isinstance(ts_raw, str):
        return None
    try:
        # JSONL timestamps end with "Z" (UTC); fromisoformat accepts "+00:00"
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return model, usage, ts


def estimate_window_cost_usd(
    projects_dir: Path,
    window: timedelta,
    now: datetime,
) -> float:
    """Sum $cost across all turns in [now-window, now] across every jsonl file.

    Ignores malformed lines and files. Returns 0.0 if projects_dir is missing.
    """
    if not projects_dir.exists():
        return 0.0
    cutoff = now - window
    total = 0.0
    for jsonl in projects_dir.rglob("*.jsonl"):
        try:
            content = jsonl.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in content.splitlines():
            parsed = parse_usage_line(line)
            if parsed is None:
                continue
            model, usage, ts = parsed
            if ts < cutoff or ts > now:
                continue
            total += compute_turn_cost(model, usage)
    return total


def estimate_quota_from_jsonl(
    projects_dir: Path,
    budget_5h_usd: float,
    budget_7d_usd: float,
    now: datetime | None = None,
) -> QuotaSnapshot:
    """Build a QuotaSnapshot from jsonl aggregation.

    The resets_at fields are approximate: we cannot know when Anthropic's
    rolling window started, so we report `now + window` as a conservative
    upper-bound (actual reset happens on or before that time).
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    spent_5h = estimate_window_cost_usd(projects_dir, timedelta(hours=5), now)
    spent_7d = estimate_window_cost_usd(projects_dir, timedelta(days=7), now)

    pct_5h = min(100.0, 100.0 * spent_5h / budget_5h_usd) if budget_5h_usd > 0 else 0.0
    pct_7d = min(100.0, 100.0 * spent_7d / budget_7d_usd) if budget_7d_usd > 0 else 0.0

    return QuotaSnapshot(
        five_hour_used_pct=pct_5h,
        five_hour_resets_at=now + timedelta(hours=5),
        seven_day_used_pct=pct_7d,
        seven_day_resets_at=now + timedelta(days=7),
        fetched_at=now,
        source="jsonl",
    )
