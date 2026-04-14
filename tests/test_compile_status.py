"""Tests for compile_status.py — human-readable sync status."""
from datetime import datetime, timezone

from compile_status import render_status
from sync_state import QueueEntry, QueueStatus, SyncState, SyncStatus


def test_render_status_summarises_running_sync():
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk="clubs",
        sleep_until=None,
        queue=[
            QueueEntry("fan-entity", "MODELS", QueueStatus.DONE, 0.09, None, None),
            QueueEntry("clubs", "SERVICES", QueueStatus.RUNNING, None, None, None),
            QueueEntry("api-gateway", "SERVICES", QueueStatus.PENDING, None, None, None),
        ],
        total_cost_usd=0.09,
        budget_used_5h_pct=45.0,
        budget_used_7d_pct=21.0,
    )
    out = render_status(state)
    assert "running" in out.lower()
    assert "1/3" in out
    assert "clubs" in out
    assert "$0.09" in out
    assert "45" in out


def test_render_status_done_state():
    state = SyncState(
        status=SyncStatus.DONE,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[
            QueueEntry("x", "SERVICES", QueueStatus.DONE, 0.10, None, None),
        ],
        total_cost_usd=0.10,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    out = render_status(state)
    assert "done" in out.lower()
    assert "1/1" in out


def test_render_status_sleeping_shows_resume_time():
    state = SyncState(
        status=SyncStatus.SLEEPING,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk=None,
        sleep_until=datetime(2026, 4, 14, 23, 30, tzinfo=timezone.utc),
        queue=[
            QueueEntry("x", "SERVICES", QueueStatus.PENDING, None, None, None),
        ],
        total_cost_usd=0.05,
        budget_used_5h_pct=90.0, budget_used_7d_pct=30.0,
    )
    out = render_status(state)
    assert "sleeping" in out.lower()
    assert "23:30" in out
