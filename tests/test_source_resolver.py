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


from provenance import SourceRef
from source_resolver import build_source_refs


def test_build_source_refs_returns_refs_with_sha(tmp_path):
    (tmp_path / "a.go").write_text("package a\n")
    (tmp_path / "b.go").write_text("package b\n")

    refs = build_source_refs([tmp_path / "a.go", tmp_path / "b.go"], tmp_path)
    assert len(refs) == 2
    assert all(isinstance(r, SourceRef) for r in refs)
    assert refs[0].path == "a.go"
    assert refs[1].path == "b.go"
    assert refs[0].sha
    assert refs[0].sha == build_source_refs([tmp_path / "a.go"], tmp_path)[0].sha


def test_build_source_refs_paths_are_repo_relative(tmp_path):
    (tmp_path / "services" / "x").mkdir(parents=True)
    (tmp_path / "services" / "x" / "main.go").write_text("")

    refs = build_source_refs([tmp_path / "services" / "x" / "main.go"], tmp_path)
    assert refs[0].path == "services/x/main.go"
    assert not refs[0].path.startswith("/")


def test_build_source_refs_sha_differs_when_content_changes(tmp_path):
    f = tmp_path / "a.go"
    f.write_text("v1")
    sha_v1 = build_source_refs([f], tmp_path)[0].sha
    f.write_text("v2")
    sha_v2 = build_source_refs([f], tmp_path)[0].sha
    assert sha_v1 != sha_v2
