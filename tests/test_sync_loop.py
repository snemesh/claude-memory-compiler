"""Tests for sync_loop.py — detached budget-aware daemon."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from llm_adapter import LLMResponse
from queue_builder import QueueItem
from quota import BudgetThresholds, QuotaSnapshot
from sync_loop import (
    LoopOutcome,
    LoopOutcomeKind,
    drive_loop,
    prime_state_from_queue,
    process_one_chunk,
)
from sync_state import (
    QueueEntry,
    QueueStatus,
    SyncState,
    SyncStatus,
    load_state,
    save_state,
)
from topo import ArticlePriority


def _state_with_queue(slugs: list[str]) -> SyncState:
    return SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[
            QueueEntry(
                slug=s, priority_tier="SERVICES",
                status=QueueStatus.PENDING,
                cost_usd=None, commit_sha=None, started_at=None,
            ) for s in slugs
        ],
        total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )


def _fake_healthy_snap():
    return QuotaSnapshot(
        five_hour_used_pct=10.0,
        five_hour_resets_at=datetime(2026, 4, 14, 8, tzinfo=timezone.utc),
        seven_day_used_pct=5.0,
        seven_day_resets_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 4, 14, 3, tzinfo=timezone.utc),
        source="jsonl",
    )


# ── process_one_chunk ──────────────────────────────────────────────

def test_process_one_chunk_continue_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    monkeypatch.setattr("sync_loop.get_quota", _fake_healthy_snap)

    def fake_llm(*a, **kw):
        (wiki / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    outcome = process_one_chunk(
        state_path=state_path,
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
        log_file=None,
        thresholds=BudgetThresholds(),
    )
    assert outcome.kind == LoopOutcomeKind.CHUNK_DONE
    assert outcome.slug == "x"
    state = load_state(state_path)
    assert state.queue[0].status == QueueStatus.DONE


def test_process_one_chunk_sleep_path(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    def fake_snap():
        return QuotaSnapshot(
            five_hour_used_pct=95.0,
            five_hour_resets_at=datetime(2026, 4, 14, 5, tzinfo=timezone.utc),
            seven_day_used_pct=40.0,
            seven_day_resets_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 4, 14, 3, tzinfo=timezone.utc),
            source="jsonl",
        )

    monkeypatch.setattr("sync_loop.get_quota", fake_snap)

    outcome = process_one_chunk(
        state_path=state_path, manifest=manifest, ripple_rules=[],
        repo_root=tmp_path, wiki_dir=tmp_path, log_file=None,
        thresholds=BudgetThresholds(five_hour_stop_pct=85.0),
    )
    assert outcome.kind == LoopOutcomeKind.SLEEP
    assert outcome.sleep_seconds > 0


def test_process_one_chunk_honors_kill_flag(tmp_path):
    state_path = tmp_path / "state.json"
    flag_path = tmp_path / "sync-state.kill"
    flag_path.write_text("stop\n")

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    outcome = process_one_chunk(
        state_path=state_path, manifest=manifest, ripple_rules=[],
        repo_root=tmp_path, wiki_dir=tmp_path, log_file=None,
        thresholds=BudgetThresholds(),
        kill_flag_path=flag_path,
    )
    assert outcome.kind == LoopOutcomeKind.KILLED


def test_process_one_chunk_queue_empty_returns_done(tmp_path):
    state_path = tmp_path / "state.json"
    save_state(state_path, _state_with_queue([]))

    outcome = process_one_chunk(
        state_path=state_path, manifest=[], ripple_rules=[],
        repo_root=tmp_path, wiki_dir=tmp_path, log_file=None,
        thresholds=BudgetThresholds(),
    )
    assert outcome.kind == LoopOutcomeKind.QUEUE_EMPTY


# ── drive_loop ────────────────────────────────────────────────────

def test_drive_loop_processes_all_then_runs_pass2_and_exits(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    monkeypatch.setattr("sync_loop.get_quota", _fake_healthy_snap)

    def fake_llm(*a, **kw):
        (wiki / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("sync_loop.time.sleep", lambda s: None)

    drive_loop(
        state_path=state_path,
        manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        log_file=None, thresholds=BudgetThresholds(),
    )

    final = load_state(state_path)
    assert final.status == SyncStatus.DONE


def test_drive_loop_stops_on_kill_flag(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    flag_path = tmp_path / "sync-state.kill"

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    flag_path.write_text("stop\n")
    monkeypatch.setattr("sync_loop.time.sleep", lambda s: None)

    drive_loop(
        state_path=state_path,
        manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        log_file=None, thresholds=BudgetThresholds(),
        kill_flag_path=flag_path,
    )

    final = load_state(state_path)
    assert final.status == SyncStatus.INTERRUPTED


# ── prime_state_from_queue ────────────────────────────────────────

def test_prime_state_from_queue_writes_initial_state(tmp_path):
    state_path = tmp_path / "state.json"
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
    ]
    prime_state_from_queue(state_path, queue)

    state = load_state(state_path)
    assert state.status == SyncStatus.PENDING
    assert len(state.queue) == 2
    assert [e.slug for e in state.queue] == ["clubs", "api-gateway"]
    assert all(e.status == QueueStatus.PENDING for e in state.queue)
