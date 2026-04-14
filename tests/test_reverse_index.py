"""Tests for reverse_index.py — file → affected articles map."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from provenance import Provenance, SourceRef, write_provenance
from reverse_index import articles_affected_by, build_reverse_index


def _make_article(path: Path, sources: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {path.stem}\n\nBody.\n", encoding="utf-8")
    write_provenance(path, Provenance(
        sources=[SourceRef(path=p, sha=s) for p, s in sources],
        compiled_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        model="sonnet-4-6",
        cost_usd=0.10,
        triggered_by="manual",
    ))


def test_build_reverse_index_empty_dir(tmp_path):
    assert build_reverse_index(tmp_path) == {}


def test_build_reverse_index_maps_files_to_articles(tmp_path):
    _make_article(tmp_path / "clubs.md", [
        ("services/clubs/entity.go", "abc"),
        ("proto/clubs/v1/clubs.proto", "def"),
    ])
    _make_article(tmp_path / "api-gateway.md", [
        ("proto/clubs/v1/clubs.proto", "def"),
        ("services/api-gateway/main.go", "xyz"),
    ])

    idx = build_reverse_index(tmp_path)
    assert idx["proto/clubs/v1/clubs.proto"] == {"clubs", "api-gateway"}
    assert idx["services/clubs/entity.go"] == {"clubs"}
    assert idx["services/api-gateway/main.go"] == {"api-gateway"}


def test_articles_affected_by_single_file(tmp_path):
    _make_article(tmp_path / "clubs.md", [
        ("services/clubs/entity.go", "abc"),
    ])
    _make_article(tmp_path / "api-gateway.md", [
        ("services/api-gateway/main.go", "xyz"),
    ])

    idx = build_reverse_index(tmp_path)
    assert articles_affected_by(idx, ["services/clubs/entity.go"]) == {"clubs"}
    assert articles_affected_by(idx, ["services/api-gateway/main.go"]) == {"api-gateway"}


def test_articles_affected_by_multiple_files(tmp_path):
    _make_article(tmp_path / "clubs.md", [("a.go", "1")])
    _make_article(tmp_path / "api-gateway.md", [("a.go", "1"), ("b.go", "2")])
    _make_article(tmp_path / "referrals.md", [("c.go", "3")])

    idx = build_reverse_index(tmp_path)
    assert articles_affected_by(idx, ["a.go", "c.go"]) == {
        "clubs", "api-gateway", "referrals",
    }


def test_articles_affected_by_unknown_file_is_empty(tmp_path):
    _make_article(tmp_path / "clubs.md", [("a.go", "1")])
    idx = build_reverse_index(tmp_path)
    assert articles_affected_by(idx, ["unrelated.go"]) == set()
