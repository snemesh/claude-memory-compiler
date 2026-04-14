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
