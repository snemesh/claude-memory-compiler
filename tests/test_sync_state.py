"""Tests for sync_state.py — atomic sync-state.json persistence."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sync_state import QueueEntry, QueueStatus, SyncState, SyncStatus, load_state, save_state


def test_sync_state_roundtrip(tmp_path):
    path = tmp_path / "sync-state.json"
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk="clubs",
        sleep_until=None,
        queue=[
            QueueEntry(slug="fan-entity", priority_tier="MODELS",
                       status=QueueStatus.DONE, cost_usd=0.09,
                       commit_sha="abc123", started_at=None),
            QueueEntry(slug="clubs", priority_tier="SERVICES",
                       status=QueueStatus.RUNNING, cost_usd=None,
                       commit_sha=None, started_at=datetime(
                           2026, 4, 14, 22, 18, tzinfo=timezone.utc)),
        ],
        total_cost_usd=0.09,
        budget_used_5h_pct=31.4,
        budget_used_7d_pct=18.2,
    )
    save_state(path, state)
    loaded = load_state(path)

    assert loaded.status == SyncStatus.RUNNING
    assert loaded.current_chunk == "clubs"
    assert len(loaded.queue) == 2
    assert loaded.queue[0].status == QueueStatus.DONE
    assert loaded.queue[0].cost_usd == 0.09
    assert loaded.total_cost_usd == 0.09


def test_load_state_missing_file_returns_none(tmp_path):
    assert load_state(tmp_path / "absent.json") is None


def test_save_state_atomic_via_tmp_rename(tmp_path):
    path = tmp_path / "sync-state.json"
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[], total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    save_state(path, state)
    assert not (tmp_path / "sync-state.json.tmp").exists()
    assert path.exists()


from sync_state import clear_kill_flag, read_kill_flag, set_kill_flag


def test_kill_flag_roundtrip(tmp_path):
    flag_path = tmp_path / "sync-state.kill"
    assert read_kill_flag(flag_path) is False
    set_kill_flag(flag_path)
    assert read_kill_flag(flag_path) is True
    clear_kill_flag(flag_path)
    assert read_kill_flag(flag_path) is False


def test_clear_kill_flag_is_idempotent(tmp_path):
    flag_path = tmp_path / "sync-state.kill"
    clear_kill_flag(flag_path)
    assert read_kill_flag(flag_path) is False
