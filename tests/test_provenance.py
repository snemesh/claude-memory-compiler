"""Tests for provenance.py — article frontmatter with source-file SHAs."""
from datetime import datetime, timezone

import pytest

from provenance import Provenance, SourceRef, read_provenance, write_provenance


def test_provenance_roundtrip(tmp_path):
    article = tmp_path / "clubs.md"
    article.write_text("# Clubs\n\nInitial body.\n", encoding="utf-8")

    prov = Provenance(
        sources=[
            SourceRef(path="services/clubs/entity.go", sha="abc123"),
            SourceRef(path="proto/clubs/v1/clubs.proto", sha="def456"),
        ],
        compiled_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        model="sonnet-4-6",
        cost_usd=0.12,
        triggered_by="manual",
    )
    write_provenance(article, prov)

    loaded = read_provenance(article)
    assert loaded is not None
    assert loaded.sources[0].path == "services/clubs/entity.go"
    assert loaded.sources[0].sha == "abc123"
    assert loaded.model == "sonnet-4-6"
    assert loaded.cost_usd == 0.12


def test_write_provenance_preserves_article_body(tmp_path):
    article = tmp_path / "clubs.md"
    article.write_text("# Clubs\n\nExisting body content.\n", encoding="utf-8")

    prov = Provenance(
        sources=[SourceRef(path="x.go", sha="abc")],
        compiled_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        model="sonnet-4-6",
        cost_usd=0.10,
        triggered_by="manual",
    )
    write_provenance(article, prov)

    content = article.read_text(encoding="utf-8")
    assert "# Clubs" in content
    assert "Existing body content." in content
    assert content.startswith("---\n")  # frontmatter first


def test_write_provenance_replaces_existing_frontmatter(tmp_path):
    article = tmp_path / "clubs.md"
    article.write_text(
        "---\nsources:\n  - path: old.go\n    sha: old\ncompiled_at: '2026-01-01T00:00:00+00:00'\nmodel: old\ncost_usd: 0.01\ntriggered_by: old\n---\n\n# Clubs\n\nBody.\n",
        encoding="utf-8",
    )

    prov = Provenance(
        sources=[SourceRef(path="new.go", sha="new")],
        compiled_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        model="sonnet-4-6",
        cost_usd=0.10,
        triggered_by="ripple:clubs",
    )
    write_provenance(article, prov)

    loaded = read_provenance(article)
    assert loaded.sources[0].path == "new.go"
    assert loaded.triggered_by == "ripple:clubs"
    assert "Body." in article.read_text(encoding="utf-8")


def test_read_provenance_no_frontmatter_returns_none(tmp_path):
    article = tmp_path / "clubs.md"
    article.write_text("# Clubs\n\nNo frontmatter.\n", encoding="utf-8")
    assert read_provenance(article) is None


def test_read_provenance_missing_file_returns_none(tmp_path):
    assert read_provenance(tmp_path / "nonexistent.md") is None
