"""Tests for source_resolver.py — ArticleSpec sources → actual files."""
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from source_resolver import resolve_sources
from topo import ArticlePriority


def test_resolve_sources_expands_globs(tmp_path):
    (tmp_path / "services" / "clubs").mkdir(parents=True)
    (tmp_path / "services" / "clubs" / "entity.go").write_text("package clubs\n")
    (tmp_path / "services" / "clubs" / "main.go").write_text("package clubs\n")
    (tmp_path / "services" / "auth").mkdir(parents=True)
    (tmp_path / "services" / "auth" / "main.go").write_text("package auth\n")

    spec = ArticleSpec(
        slug="clubs",
        priority=ArticlePriority.SERVICES,
        sources=["services/clubs/**/*.go"],
    )
    paths = resolve_sources(spec, tmp_path)
    rel = sorted(str(p.relative_to(tmp_path)) for p in paths)
    assert rel == ["services/clubs/entity.go", "services/clubs/main.go"]


def test_resolve_sources_deduplicates_across_globs(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.go").write_text("")
    spec = ArticleSpec(
        slug="x",
        priority=ArticlePriority.SERVICES,
        sources=["x/*.go", "x/**/*.go"],
    )
    paths = resolve_sources(spec, tmp_path)
    assert len(paths) == 1


def test_resolve_sources_returns_sorted_for_determinism(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "z.go").write_text("")
    (tmp_path / "x" / "a.go").write_text("")
    (tmp_path / "x" / "m.go").write_text("")
    spec = ArticleSpec(
        slug="x",
        priority=ArticlePriority.SERVICES,
        sources=["x/*.go"],
    )
    paths = resolve_sources(spec, tmp_path)
    names = [p.name for p in paths]
    assert names == ["a.go", "m.go", "z.go"]


def test_resolve_sources_empty_when_no_match(tmp_path):
    spec = ArticleSpec(
        slug="x",
        priority=ArticlePriority.SERVICES,
        sources=["nonexistent/**/*.go"],
    )
    assert resolve_sources(spec, tmp_path) == []
