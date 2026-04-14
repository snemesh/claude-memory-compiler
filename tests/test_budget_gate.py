"""Tests for budget_gate.py — pre-chunk quota decisions."""
from datetime import datetime, timedelta, timezone

import pytest

from budget_gate import Decision, DecisionKind, decide
from quota import BudgetThresholds, QuotaSnapshot


def _snap(five_pct=50.0, seven_pct=30.0, resets_in_h=2.0):
    now = datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc)
    return QuotaSnapshot(
        five_hour_used_pct=five_pct,
        five_hour_resets_at=now + timedelta(hours=resets_in_h),
        seven_day_used_pct=seven_pct,
        seven_day_resets_at=now + timedelta(days=3),
        fetched_at=now,
        source="jsonl",
    )


def test_decide_continue_under_threshold():
    snap = _snap(five_pct=60.0, seven_pct=40.0)
    thresh = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    d = decide(snap, thresh)
    assert d.kind == DecisionKind.CONTINUE
    assert d.sleep_seconds == 0


def test_decide_sleep_on_five_hour_exhaust():
    snap = _snap(five_pct=90.0, resets_in_h=1.5)
    thresh = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    d = decide(snap, thresh)
    assert d.kind == DecisionKind.SLEEP
    assert 5400 < d.sleep_seconds < 5500
    assert "five_hour" in d.reason


def test_decide_abort_on_seven_day_exhaust():
    snap = _snap(five_pct=40.0, seven_pct=95.0)
    thresh = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    d = decide(snap, thresh)
    assert d.kind == DecisionKind.ABORT
    assert "seven_day" in d.reason


def test_decide_abort_on_missing_snapshot():
    thresh = BudgetThresholds()
    d = decide(None, thresh)
    assert d.kind == DecisionKind.ABORT
    assert "unavailable" in d.reason.lower()
