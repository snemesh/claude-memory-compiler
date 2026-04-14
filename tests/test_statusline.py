"""Tests for statusline.py — one-line progress format."""
from datetime import datetime, timezone

from statusline import render_statusline
from sync_state import QueueEntry, QueueStatus, SyncState, SyncStatus


def test_render_statusline_progress_bar_reflects_done_ratio():
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk="x", sleep_until=None,
        queue=[
            QueueEntry(f"a{i}", "SERVICES",
                       QueueStatus.DONE if i < 5 else QueueStatus.PENDING,
                       0.1 if i < 5 else None, None, None)
            for i in range(10)
        ],
        total_cost_usd=0.5,
        budget_used_5h_pct=40.0, budget_used_7d_pct=10.0,
    )
    out = render_statusline(state)
    assert "5/10" in out
    assert "50%" in out or "50.0%" in out
    assert "$0.5" in out or "$0.50" in out


def test_render_statusline_includes_budget():
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[QueueEntry("x", "SERVICES", QueueStatus.DONE, 0.1, None, None)],
        total_cost_usd=0.1,
        budget_used_5h_pct=68.2, budget_used_7d_pct=23.4,
    )
    out = render_statusline(state)
    assert "5h:68" in out
    assert "7d:23" in out


def test_render_statusline_sleeping_shown_explicitly():
    state = SyncState(
        status=SyncStatus.SLEEPING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None,
        sleep_until=datetime(2026, 4, 14, 23, 30, tzinfo=timezone.utc),
        queue=[QueueEntry("x", "SERVICES", QueueStatus.PENDING, None, None, None)],
        total_cost_usd=0.0,
        budget_used_5h_pct=92.0, budget_used_7d_pct=40.0,
    )
    out = render_statusline(state)
    assert "sleep" in out.lower()


def test_render_statusline_handles_empty_queue():
    state = SyncState(
        status=SyncStatus.DONE,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[], total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    out = render_statusline(state)
    assert "0/0" in out
