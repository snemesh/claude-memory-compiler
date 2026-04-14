"""Tests for validator.py — Pass 3 Haiku cross-article validation."""
import json
from pathlib import Path

import pytest

from llm_adapter import LLMResponse
from validator import (
    ValidationIssue,
    ValidationResult,
    build_validation_prompt,
    parse_validation_response,
    validate_article,
)


# ── Prompt builder ─────────────────────────────────────────────────

def test_build_validation_prompt_includes_article_and_siblings(tmp_path):
    (tmp_path / "clubs.md").write_text("# Clubs\n\nClubs are organizations.\n")
    (tmp_path / "api-gateway.md").write_text("# API\n\nRoutes requests.\n")
    (tmp_path / "overview.md").write_text("# Overview\n\nSystem design.\n")

    prompt = build_validation_prompt(
        article_slug="clubs",
        wiki_dir=tmp_path,
        sibling_slugs=["api-gateway"],
    )
    assert "Clubs are organizations" in prompt
    assert "Routes requests" in prompt
    assert "System design" in prompt
    assert "clubs" in prompt
    assert "api-gateway" in prompt


def test_build_validation_prompt_asks_for_structured_issues(tmp_path):
    (tmp_path / "x.md").write_text("# X\n")
    prompt = build_validation_prompt(article_slug="x",
                                     wiki_dir=tmp_path,
                                     sibling_slugs=[])
    lower = prompt.lower()
    assert "json" in lower or "list" in lower
    assert "contradiction" in lower or "inconsistency" in lower


def test_build_validation_prompt_omits_missing_sibling_gracefully(tmp_path):
    (tmp_path / "clubs.md").write_text("# Clubs\n")
    prompt = build_validation_prompt(
        article_slug="clubs",
        wiki_dir=tmp_path,
        sibling_slugs=["api-gateway"],
    )
    assert "clubs" in prompt


# ── Response parsing ───────────────────────────────────────────────

def test_parse_validation_response_parses_json_array():
    text = '''Some preamble.
```json
[
  {"kind": "contradiction", "description": "X says A, Y says B", "evidence": "..."}
]
```
Trailing text.'''
    issues = parse_validation_response(text)
    assert len(issues) == 1
    assert issues[0].kind == "contradiction"
    assert "X says A" in issues[0].description


def test_parse_validation_response_empty_array_is_ok():
    text = "Here is the result:\n```json\n[]\n```"
    assert parse_validation_response(text) == []


def test_parse_validation_response_no_json_returns_empty_with_warning():
    text = "I could not analyze this article."
    assert parse_validation_response(text) == []


# ── validate_article ──────────────────────────────────────────────

def test_validate_article_calls_llm_and_returns_issues(tmp_path, monkeypatch):
    (tmp_path / "clubs.md").write_text("# Clubs\n\nBody.\n")
    (tmp_path / "api-gateway.md").write_text("# API\n\nBody.\n")

    fake_response = LLMResponse(
        text='```json\n[{"kind": "stale", "description": "Z", "evidence": "q"}]\n```',
        cost_usd=0.003,
        model="claude-haiku-4-5",
    )
    monkeypatch.setattr("validator.call_llm", lambda *a, **kw: fake_response)

    result = validate_article(
        article_slug="clubs",
        wiki_dir=tmp_path,
        sibling_slugs=["api-gateway"],
    )
    assert isinstance(result, ValidationResult)
    assert result.slug == "clubs"
    assert len(result.issues) == 1
    assert result.issues[0].kind == "stale"
    assert result.cost_usd == 0.003


def test_validate_article_uses_haiku_model(tmp_path, monkeypatch):
    (tmp_path / "x.md").write_text("# X\n")
    captured = {}

    def fake_llm(prompt, model, cwd, max_turns=30, allowed_tools=None):
        captured["model"] = model
        captured["allowed_tools"] = allowed_tools
        return LLMResponse(text="[]", cost_usd=0.0, model=model)

    monkeypatch.setattr("validator.call_llm", fake_llm)

    validate_article(article_slug="x", wiki_dir=tmp_path, sibling_slugs=[])
    assert "haiku" in captured["model"].lower()
    # Validator must disable tools to prevent Haiku from reading files /
    # wandering off into multi-turn tool use.
    assert captured["allowed_tools"] == []
