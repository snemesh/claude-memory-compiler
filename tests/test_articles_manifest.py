"""Tests for articles_manifest.py — wiki-articles.yaml loader."""
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec, load_manifest
from topo import ArticlePriority


def test_load_manifest_parses_specs(tmp_path):
    cfg = tmp_path / "wiki-articles.yaml"
    cfg.write_text(
        """
articles:
  - slug: clubs
    priority: SERVICES
    sources: ["services/clubs/**/*.go"]
  - slug: fan-entity
    priority: MODELS
    sources: ["proto/user/**/*.proto"]
""",
        encoding="utf-8",
    )
    specs = load_manifest(cfg)
    assert len(specs) == 2
    by_slug = {s.slug: s for s in specs}
    assert by_slug["clubs"].priority == ArticlePriority.SERVICES
    assert by_slug["clubs"].sources == ["services/clubs/**/*.go"]
    assert by_slug["fan-entity"].priority == ArticlePriority.MODELS


def test_load_manifest_invalid_priority_raises(tmp_path):
    cfg = tmp_path / "wiki-articles.yaml"
    cfg.write_text(
        """
articles:
  - slug: bad
    priority: INVALID_TIER
    sources: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="INVALID_TIER"):
        load_manifest(cfg)


def test_load_manifest_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nonexistent.yaml")
