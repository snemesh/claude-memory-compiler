"""Tests for jsonl_quota.py — local token log → cost estimate."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jsonl_quota import (
    ModelPricing,
    compute_turn_cost,
    estimate_quota_from_jsonl,
    estimate_window_cost_usd,
    parse_usage_line,
)
from quota import QuotaSnapshot


# ── Pricing ───────────────────────────────────────────────────────

def test_compute_turn_cost_sonnet():
    usage = {
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    cost = compute_turn_cost(model="claude-sonnet-4-6", usage=usage)
    # 100 * $3/MTok + 200 * $15/MTok = 0.0003 + 0.003 = 0.0033
    assert cost == pytest.approx(0.0033, rel=1e-3)


def test_compute_turn_cost_with_cache_reads():
    usage = {
        "input_tokens": 10,
        "output_tokens": 50,
        "cache_read_input_tokens": 10000,
        "cache_creation_input_tokens": 0,
    }
    cost = compute_turn_cost(model="claude-sonnet-4-6", usage=usage)
    # Sonnet: 10*$3/MTok + 50*$15/MTok + 10000*$0.30/MTok = 3e-5 + 7.5e-4 + 3e-3
    assert cost == pytest.approx(3e-5 + 7.5e-4 + 3e-3, rel=1e-3)


def test_compute_turn_cost_cache_write_5m_and_1h():
    """5m cache write = 1.25x input; 1h cache write = 2x input."""
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 10000,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 4000,
            "ephemeral_1h_input_tokens": 6000,
        },
    }
    cost = compute_turn_cost(model="claude-sonnet-4-6", usage=usage)
    # 4000 * $3.75/MTok (5m) + 6000 * $6/MTok (1h)
    expected = 4000 * 3.75e-6 + 6000 * 6.0e-6
    assert cost == pytest.approx(expected, rel=1e-3)


def test_compute_turn_cost_unknown_model_uses_sonnet_pricing():
    usage = {"input_tokens": 100, "output_tokens": 200,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    cost = compute_turn_cost(model="some-future-model", usage=usage)
    # Same as sonnet
    assert cost == pytest.approx(0.0033, rel=1e-3)


def test_compute_turn_cost_opus_more_expensive_than_sonnet():
    usage = {"input_tokens": 1000, "output_tokens": 1000,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    sonnet = compute_turn_cost(model="claude-sonnet-4-6", usage=usage)
    opus = compute_turn_cost(model="claude-opus-4-6", usage=usage)
    assert opus > sonnet * 4  # Opus is ~5x more expensive


# ── Line parsing ──────────────────────────────────────────────────

def test_parse_usage_line_extracts_model_usage_and_ts():
    line = (
        '{"timestamp": "2026-04-14T02:45:29.698Z", '
        '"message": {"model": "claude-sonnet-4-6", '
        '"usage": {"input_tokens": 10, "output_tokens": 20}}}'
    )
    parsed = parse_usage_line(line)
    assert parsed is not None
    model, usage, ts = parsed
    assert model == "claude-sonnet-4-6"
    assert usage["input_tokens"] == 10
    assert ts == datetime(2026, 4, 14, 2, 45, 29, 698000, tzinfo=timezone.utc)


def test_parse_usage_line_no_message_returns_none():
    assert parse_usage_line('{"type": "user", "content": "hi"}') is None


def test_parse_usage_line_no_usage_returns_none():
    assert parse_usage_line('{"timestamp": "2026-04-14T02:45:29Z", "message": {"model": "x"}}') is None


def test_parse_usage_line_malformed_json_returns_none():
    assert parse_usage_line("not json at all") is None


# ── Window aggregation ────────────────────────────────────────────

def _make_usage_line(ts: datetime, model: str = "claude-sonnet-4-6",
                     input_t: int = 1000, output_t: int = 500) -> str:
    import json
    return json.dumps({
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    })


def test_estimate_window_cost_usd_aggregates_across_files(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    proj_a = tmp_path / "proj-a"
    proj_a.mkdir()
    (proj_a / "session1.jsonl").write_text(
        _make_usage_line(now - timedelta(hours=1), input_t=1000, output_t=1000) + "\n" +
        _make_usage_line(now - timedelta(minutes=30), input_t=1000, output_t=1000) + "\n",
    )
    proj_b = tmp_path / "proj-b"
    proj_b.mkdir()
    (proj_b / "session2.jsonl").write_text(
        _make_usage_line(now - timedelta(hours=2), input_t=1000, output_t=1000) + "\n",
    )

    total = estimate_window_cost_usd(tmp_path, window=timedelta(hours=5), now=now)
    # 3 turns × Sonnet × (1000*$3e-6 + 1000*$15e-6) = 3 × 0.018 = 0.054
    assert total == pytest.approx(3 * (1000 * 3e-6 + 1000 * 15e-6), rel=1e-3)


def test_estimate_window_cost_usd_excludes_old_entries(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        _make_usage_line(now - timedelta(hours=1)) + "\n" +
        _make_usage_line(now - timedelta(hours=10)) + "\n"  # outside 5h
    )
    total = estimate_window_cost_usd(tmp_path, window=timedelta(hours=5), now=now)
    # Only 1 entry counts
    expected = 1000 * 3e-6 + 500 * 15e-6
    assert total == pytest.approx(expected, rel=1e-3)


def test_estimate_window_cost_usd_empty_dir_is_zero(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    assert estimate_window_cost_usd(tmp_path, window=timedelta(hours=5), now=now) == 0.0


# ── Quota snapshot ────────────────────────────────────────────────

def test_estimate_quota_from_jsonl_produces_snapshot(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        _make_usage_line(now - timedelta(minutes=10), input_t=100_000, output_t=100_000) + "\n"
    )
    snap = estimate_quota_from_jsonl(
        projects_dir=tmp_path,
        budget_5h_usd=10.0,
        budget_7d_usd=140.0,
        now=now,
    )
    assert snap is not None
    assert snap.source == "jsonl"
    # Cost: 100k*$3e-6 + 100k*$15e-6 = 0.3 + 1.5 = 1.8
    # 1.8 / 10 = 18%
    assert snap.five_hour_used_pct == pytest.approx(18.0, rel=1e-2)
    # resets_at ~= now + 5h (approximate — exact rolling boundary isn't meaningful)
    delta_5h = (snap.five_hour_resets_at - now).total_seconds()
    assert 4.9 * 3600 < delta_5h < 5.1 * 3600


def test_estimate_quota_caps_at_100_percent(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    # Massive usage that would exceed budget
    jsonl.write_text(
        _make_usage_line(now - timedelta(minutes=10),
                         input_t=10_000_000, output_t=10_000_000) + "\n"
    )
    snap = estimate_quota_from_jsonl(
        projects_dir=tmp_path,
        budget_5h_usd=10.0,
        budget_7d_usd=140.0,
        now=now,
    )
    assert snap.five_hour_used_pct == 100.0


# ── Pricing constants exist ───────────────────────────────────────

def test_model_pricing_has_all_major_models():
    from jsonl_quota import _PRICING
    assert "claude-sonnet-4-6" in _PRICING
    assert "claude-opus-4-6" in _PRICING
    assert "claude-haiku-4-5" in _PRICING


# ── Session-aware cost (#3 window alignment) ──────────────────────

from jsonl_quota import estimate_session_cost_usd


def test_estimate_session_cost_only_sums_current_session(tmp_path):
    """Activity before an idle gap > idle_gap_s must not count."""
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    # Turn 1: 4h30m ago (old session)
    # ... 75-minute idle gap ...
    # Turn 2: 3h15m ago (new session starts)
    # Turn 3: 3h5m ago (10-minute gap — same session as turn 2)
    jsonl.write_text(
        _make_usage_line(now - timedelta(hours=4, minutes=30),
                         input_t=1000, output_t=1000) + "\n" +
        _make_usage_line(now - timedelta(hours=3, minutes=15),
                         input_t=1000, output_t=1000) + "\n" +
        _make_usage_line(now - timedelta(hours=3, minutes=5),
                         input_t=1000, output_t=1000) + "\n"
    )

    total = estimate_session_cost_usd(
        tmp_path, now=now, idle_gap_s=1800,
    )
    # Turns 2+3 count → 2 × sonnet ($3+$15 per MTok) × (1000+1000 tokens)
    assert total == pytest.approx(2 * (1000 * 3e-6 + 1000 * 15e-6), rel=1e-3)


def test_estimate_session_cost_single_session_no_gap(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        "\n".join(
            _make_usage_line(now - timedelta(minutes=20 * i),
                             input_t=1000, output_t=1000)
            for i in range(3, 0, -1)
        )
    )
    total = estimate_session_cost_usd(tmp_path, now=now, idle_gap_s=1800)
    # No 30-min gap anywhere → all 3 turns count
    assert total == pytest.approx(3 * (1000 * 3e-6 + 1000 * 15e-6), rel=1e-3)


def test_estimate_session_cost_empty_returns_zero(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    assert estimate_session_cost_usd(tmp_path, now=now) == 0.0


# ── Model filter (#1 Sonnet split) ────────────────────────────────

from jsonl_quota import sonnet_only


def test_sonnet_only_filter():
    assert sonnet_only("claude-sonnet-4-6") is True
    assert sonnet_only("claude-sonnet-4-6[1m]") is True
    assert sonnet_only("claude-opus-4-6") is False
    assert sonnet_only("claude-haiku-4-5") is False


def test_estimate_window_cost_with_model_filter(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        _make_usage_line(now - timedelta(hours=1),
                         model="claude-sonnet-4-6",
                         input_t=1_000_000, output_t=0) + "\n" +
        _make_usage_line(now - timedelta(hours=1),
                         model="claude-opus-4-6",
                         input_t=1_000_000, output_t=0) + "\n"
    )
    # Sonnet: 1M * $3 = $3
    # Opus: 1M * $15 = $15
    # All: $18
    from jsonl_quota import estimate_window_cost_usd
    total_all = estimate_window_cost_usd(tmp_path, timedelta(hours=5), now)
    total_sonnet = estimate_window_cost_usd(
        tmp_path, timedelta(hours=5), now, model_filter=sonnet_only,
    )
    assert total_all == pytest.approx(18.0, rel=1e-3)
    assert total_sonnet == pytest.approx(3.0, rel=1e-3)


# ── QuotaSnapshot enrichment ──────────────────────────────────────

def test_estimate_quota_from_jsonl_populates_session_and_sonnet_fields(tmp_path):
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        _make_usage_line(now - timedelta(minutes=10),
                         model="claude-sonnet-4-6",
                         input_t=100_000, output_t=100_000) + "\n"
    )
    snap = estimate_quota_from_jsonl(
        projects_dir=tmp_path,
        budget_5h_usd=10.0,
        budget_7d_usd=140.0,
        now=now,
    )
    # $1.80 spent, both session and rolling windows cover this
    assert snap.five_hour_used_pct == pytest.approx(18.0, rel=1e-2)
    assert snap.five_hour_session_used_pct == pytest.approx(18.0, rel=1e-2)
    # All traffic is Sonnet → sonnet % matches all-models %
    assert snap.seven_day_sonnet_used_pct == pytest.approx(
        snap.seven_day_used_pct, rel=1e-2,
    )
