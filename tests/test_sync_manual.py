"""Tests for sync.py manual-mode preflight and execution."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from llm_adapter import LLMResponse
from pipeline import PipelineReport
from queue_builder import QueueItem
from quota import QuotaSnapshot
from sync import PreflightSummary, preflight_summary, run_manual_sync
from sync_state import SyncStatus, load_state
from topo import ArticlePriority


def _snap(five_pct=50.0, seven_pct=30.0):
    now = datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc)
    return QuotaSnapshot(
        five_hour_used_pct=five_pct,
        five_hour_resets_at=now + timedelta(hours=2),
        seven_day_used_pct=seven_pct,
        seven_day_resets_at=now + timedelta(days=3),
        fetched_at=now,
        source="jsonl",
    )


def test_preflight_summary_renders_queue_and_cost():
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
    ]
    summary = preflight_summary(queue=queue, avg_cost=0.30, snap=_snap())
    assert isinstance(summary, PreflightSummary)
    assert summary.articles == 2
    assert summary.estimated_cost_usd == pytest.approx(0.60)
    assert "clubs" in summary.text
    assert "api-gateway" in summary.text
    assert "5h" in summary.text


def test_preflight_summary_handles_no_quota():
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]
    summary = preflight_summary(queue=queue, avg_cost=0.10, snap=None)
    assert summary.articles == 1
    assert "quota unavailable" in summary.text.lower()


def test_run_manual_sync_invokes_pipeline_and_writes_state(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.001, model="claude-haiku-4-5")
        (cwd / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_manual_sync(
        queue=queue,
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
        log_file=wiki / "log.md",
        state_path=state_path,
    )

    assert isinstance(report, PipelineReport)
    assert report.articles_compiled == 1
    state = load_state(state_path)
    assert state.status == SyncStatus.DONE
    assert state.total_cost_usd == report.total_cost_usd


def test_run_manual_sync_marks_failed_on_exception(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]

    def fake_llm(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    with pytest.raises(RuntimeError):
        run_manual_sync(
            queue=queue, manifest=manifest, ripple_rules=[],
            repo_root=repo, wiki_dir=wiki,
            log_file=wiki / "log.md", state_path=state_path,
        )

    state = load_state(state_path)
    assert state.status == SyncStatus.FAILED
