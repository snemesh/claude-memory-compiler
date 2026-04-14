"""Tests for article_compiler.py — Pass 1 single-article compilation."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from article_compiler import build_pass1_prompt
from articles_manifest import ArticleSpec
from provenance import SourceRef
from topo import ArticlePriority


def test_build_pass1_prompt_mentions_slug_and_priority():
    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES,
                       sources=["services/clubs/**"])
    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=[SourceRef(path="services/clubs/entity.go", sha="abc")],
        source_contents={"services/clubs/entity.go": "package clubs"},
        glossary_block="## Pinned\n\nBounded contexts...",
        existing_body=None,
        trigger="manual",
    )
    assert "clubs" in prompt
    assert "SERVICES" in prompt
    assert "Bounded contexts" in prompt
    assert "package clubs" in prompt


def test_build_pass1_prompt_includes_existing_body_when_provided():
    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES, sources=[])
    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=[],
        source_contents={},
        glossary_block="",
        existing_body="# Clubs\n\nPrevious content that should be refined.\n",
        trigger="ripple:api-gateway",
    )
    assert "Previous content" in prompt
    assert "ripple:api-gateway" in prompt


def test_build_pass1_prompt_without_existing_body_requests_creation():
    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES, sources=[])
    prompt = build_pass1_prompt(
        spec=spec, source_refs=[], source_contents={},
        glossary_block="", existing_body=None, trigger="manual",
    )
    assert "create" in prompt.lower() or "new" in prompt.lower()


def test_build_pass1_prompt_truncates_huge_source_file():
    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=[])
    big = "x" * 200_000
    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=[SourceRef(path="big.go", sha="abc")],
        source_contents={"big.go": big},
        glossary_block="",
        existing_body=None,
        trigger="manual",
    )
    assert len(prompt) < 150_000


from article_compiler import CompileResult, compile_article
from llm_adapter import LLMResponse
from queue_builder import QueueItem


def test_compile_article_writes_body_and_provenance(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / "services" / "clubs").mkdir(parents=True)
    (repo / "services" / "clubs" / "entity.go").write_text("package clubs\n")

    spec = ArticleSpec(
        slug="clubs",
        priority=ArticlePriority.SERVICES,
        sources=["services/clubs/**/*.go"],
    )
    item = QueueItem(slug="clubs", priority=ArticlePriority.SERVICES, trigger="manual")

    def fake_llm(prompt, model, cwd, max_turns=30):
        article_path = wiki / "clubs.md"
        article_path.write_text("# Clubs\n\nClubs are organizations...\n", encoding="utf-8")
        return LLMResponse(text="Wrote clubs.md", cost_usd=0.12, model=model)

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    result = compile_article(
        item=item,
        spec=spec,
        repo_root=repo,
        wiki_dir=wiki,
        glossary_block="## Pinned\n",
    )

    assert isinstance(result, CompileResult)
    assert result.cost_usd == 0.12
    assert (wiki / "clubs.md").exists()

    content = (wiki / "clubs.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "Clubs are organizations" in content
    assert "services/clubs/entity.go" in content


def test_compile_article_preserves_existing_body_when_llm_skips_write(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / "x.go").write_text("package x\n")

    (wiki / "clubs.md").write_text("# Clubs\n\nExisting content\n", encoding="utf-8")

    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES,
                       sources=["x.go"])
    item = QueueItem("clubs", ArticlePriority.SERVICES, "manual")

    def fake_llm(prompt, model, cwd, max_turns=30):
        return LLMResponse(text="No changes needed", cost_usd=0.02, model=model)

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    compile_article(
        item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
        glossary_block="",
    )

    content = (wiki / "clubs.md").read_text(encoding="utf-8")
    assert "Existing content" in content
    assert content.startswith("---\n")


def test_compile_article_records_trigger_in_provenance(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    (repo / "x.go").write_text("")

    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=["x.go"])
    item = QueueItem("x", ArticlePriority.SERVICES, "ripple:api-gateway")

    monkeypatch.setattr("article_compiler.call_llm",
                        lambda *a, **kw: LLMResponse("ok", 0.01, "claude-sonnet-4-6"))

    compile_article(item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
                    glossary_block="")

    content = (wiki / "x.md").read_text(encoding="utf-8")
    assert "ripple:api-gateway" in content


from validator import ValidationIssue


def test_compile_article_includes_feedback_in_prompt(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    (repo / "x.go").write_text("")

    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=["x.go"])
    item = QueueItem("x", ArticlePriority.SERVICES, "manual")

    captured_prompt = {}

    def fake_llm(prompt, model, cwd, max_turns=30):
        captured_prompt["p"] = prompt
        (wiki / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    compile_article(
        item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
        glossary_block="",
        feedback_issues=[
            ValidationIssue(
                kind="stale",
                description="Article says GET-only but code accepts all methods",
                evidence="table shows GET; handler has no method check",
            ),
        ],
    )
    assert "stale" in captured_prompt["p"]
    assert "GET-only" in captured_prompt["p"]


def test_compile_article_appends_log_entry(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    log_file = wiki / "log.md"
    (repo / "x.go").write_text("")

    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=["x.go"])
    item = QueueItem("x", ArticlePriority.SERVICES, "manual")

    monkeypatch.setattr("article_compiler.call_llm",
                        lambda *a, **kw: LLMResponse("ok", 0.07, "claude-sonnet-4-6"))

    compile_article(item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
                    glossary_block="", log_file=log_file)

    content = log_file.read_text(encoding="utf-8")
    assert "# Wiki Operations Log" in content
    assert "ingest" in content
    assert "x" in content
    assert "cost_usd=0.07" in content
    assert "sources=1" in content
