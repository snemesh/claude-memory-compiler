"""Tests for pipeline.py — orchestrates Pass 1 + Pass 2 + Pass 3 over a queue."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from llm_adapter import LLMResponse
from pipeline import PipelineReport, run_pipeline
from queue_builder import QueueItem
from ripple_map import RippleRule
from topo import ArticlePriority


@pytest.fixture
def repo_and_wiki(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "services" / "clubs").mkdir(parents=True)
    (repo / "services" / "clubs" / "entity.go").write_text("package clubs\n")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    return repo, wiki


@pytest.fixture
def manifest():
    return [
        ArticleSpec("clubs", ArticlePriority.SERVICES, ["services/clubs/**/*.go"]),
        ArticleSpec("api-gateway", ArticlePriority.SERVICES, ["services/*/**/*.go"]),
    ]


def _slug_from_prompt(prompt: str) -> str:
    """Extract article slug from the Pass 1 prompt's first line."""
    first_line = prompt.split("\n", 1)[0]
    # Pass 1 starts with: "# Compile wiki article: {slug}"
    return first_line.rsplit(":", 1)[-1].strip()


def test_run_pipeline_processes_every_queue_item(repo_and_wiki, manifest, monkeypatch):
    repo, wiki = repo_and_wiki
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
    ]

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate wiki article" in prompt:
            return LLMResponse(text="```json\n[]\n```", cost_usd=0.002,
                               model="claude-haiku-4-5")
        slug = _slug_from_prompt(prompt)
        article_path = cwd / f"{slug}.md"
        article_path.write_text(f"# {slug}\n\nBody.\n", encoding="utf-8")
        return LLMResponse(text=f"wrote {slug}", cost_usd=0.10,
                           model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_pipeline(
        queue=queue,
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
    )

    assert isinstance(report, PipelineReport)
    assert report.articles_compiled == 2
    assert report.total_cost_usd == pytest.approx(0.10 * 2 + 0.002 * 2, rel=1e-3)
    assert (wiki / "clubs.md").exists()
    assert (wiki / "api-gateway.md").exists()


def test_run_pipeline_emits_log_entries(repo_and_wiki, manifest, monkeypatch):
    repo, wiki = repo_and_wiki
    log_file = wiki / "log.md"

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.001, model="claude-haiku-4-5")
        slug = _slug_from_prompt(prompt)
        (cwd / f"{slug}.md").write_text(f"# {slug}\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.05, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    run_pipeline(
        queue=[QueueItem("clubs", ArticlePriority.SERVICES, "manual")],
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
        log_file=log_file,
    )

    log_content = log_file.read_text(encoding="utf-8")
    assert "ingest" in log_content
    assert "clubs" in log_content


def test_run_pipeline_runs_pass2_at_end(repo_and_wiki, manifest, monkeypatch):
    repo, wiki = repo_and_wiki

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.0, model="claude-haiku-4-5")
        slug = _slug_from_prompt(prompt)
        sibling = "api-gateway" if slug == "clubs" else "clubs"
        (cwd / f"{slug}.md").write_text(
            f"# {slug}\n\nThis references {sibling} in plain text.\n",
            encoding="utf-8",
        )
        return LLMResponse(text="ok", cost_usd=0.05, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_pipeline(
        queue=[
            QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
            QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
        ],
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
    )
    assert "[[api-gateway]]" in (wiki / "clubs.md").read_text()
    assert "[[clubs]]" in (wiki / "api-gateway.md").read_text()
    assert report.links_added >= 2


def test_run_pipeline_empty_queue_returns_zero_report(repo_and_wiki, manifest):
    repo, wiki = repo_and_wiki
    report = run_pipeline(
        queue=[], manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
    )
    assert report.articles_compiled == 0
    assert report.total_cost_usd == 0.0
