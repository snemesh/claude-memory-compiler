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

    def fake_llm(prompt, model, cwd, max_turns=30, allowed_tools=None):
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


def test_run_manual_sync_updates_state_between_chunks(tmp_path, monkeypatch):
    """After each Pass 1 compile, state.json reflects that entry as DONE
    with its cost filled in — so compile-status can render live progress."""
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "a.go").write_text("")
    (repo / "b.go").write_text("")

    manifest = [
        ArticleSpec("a", ArticlePriority.SERVICES, ["a.go"]),
        ArticleSpec("b", ArticlePriority.SERVICES, ["b.go"]),
    ]
    queue = [
        QueueItem("a", ArticlePriority.SERVICES, "manual"),
        QueueItem("b", ArticlePriority.SERVICES, "manual"),
    ]

    snapshots: list[dict] = []

    def fake_llm(prompt, model, cwd, max_turns=30, allowed_tools=None):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.001, model="claude-haiku-4-5")
        slug_line = prompt.split("\n", 1)[0]
        slug = slug_line.rsplit(":", 1)[-1].strip()
        (cwd / f"{slug}.md").write_text(f"# {slug}\n\nBody.\n")
        # Capture state AFTER this compile returns (callback fires next)
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    # Spy wrapper: after each Pass 1 callback, snapshot state
    import sync as sync_mod
    real_cb = sync_mod._on_chunk_done

    def spy_cb(path, slug, result):
        real_cb(path, slug, result)
        loaded = load_state(path)
        snapshots.append({
            "done": [e.slug for e in loaded.queue if e.status.value == "done"],
            "pending": [e.slug for e in loaded.queue if e.status.value == "pending"],
            "total_cost": loaded.total_cost_usd,
        })

    monkeypatch.setattr("sync._on_chunk_done", spy_cb)
    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    run_manual_sync(
        queue=queue, manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        log_file=None, state_path=state_path,
    )

    # Two snapshots taken (one per Pass 1 compile)
    assert len(snapshots) == 2
    assert snapshots[0]["done"] == ["a"]
    assert snapshots[0]["pending"] == ["b"]
    assert snapshots[0]["total_cost"] == pytest.approx(0.10)
    assert snapshots[1]["done"] == ["a", "b"]
    assert snapshots[1]["pending"] == []
    assert snapshots[1]["total_cost"] == pytest.approx(0.20)


def test_run_manual_sync_final_state_keeps_per_entry_cost(tmp_path, monkeypatch):
    """Final state must retain per-entry cost_usd from the live updates,
    not blank them out (old behavior wiped cost in the finalize step)."""
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]

    def fake_llm(prompt, model, cwd, max_turns=30, allowed_tools=None):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.001, model="claude-haiku-4-5")
        (cwd / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.23, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    run_manual_sync(
        queue=queue, manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        log_file=None, state_path=state_path,
    )

    state = load_state(state_path)
    assert state.queue[0].cost_usd == pytest.approx(0.23)
    # Final total_cost reconciled from report (pass1 + haiku validate)
    assert state.total_cost_usd == pytest.approx(0.23 + 0.001, rel=1e-3)


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
