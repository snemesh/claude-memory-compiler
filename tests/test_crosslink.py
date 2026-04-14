"""Tests for crosslink.py — Pass 2 wiki-link scanner and applier."""
from pathlib import Path

import pytest

from crosslink import (
    Pass2Report,
    apply_crosslinks,
    find_dangling_refs,
    find_unlinked_mentions,
    run_pass2,
)


# ── find_unlinked_mentions ─────────────────────────────────────────

def test_find_unlinked_mentions_basic():
    text = "See the clubs service and the api-gateway for details."
    mentions = find_unlinked_mentions(text, known_slugs=["clubs", "api-gateway"])
    slugs_found = {m[0] for m in mentions}
    assert slugs_found == {"clubs", "api-gateway"}


def test_find_unlinked_mentions_ignores_already_linked():
    text = "See [[clubs]] and also clubs again in text."
    mentions = find_unlinked_mentions(text, known_slugs=["clubs"])
    assert len(mentions) == 1


def test_find_unlinked_mentions_respects_word_boundaries():
    text = "Clubhouse is not the same."
    mentions = find_unlinked_mentions(text, known_slugs=["club"])
    assert mentions == []


def test_find_unlinked_mentions_returns_positions():
    text = "clubs and api-gateway"
    mentions = find_unlinked_mentions(text, known_slugs=["clubs", "api-gateway"])
    assert len(mentions) == 2


def test_find_unlinked_mentions_ignores_code_blocks():
    text = """See clubs in prose.

```go
// The clubs variable in code should not be linked.
type Clubs struct{}
```

But clubs outside code should match."""
    mentions = find_unlinked_mentions(text, known_slugs=["clubs"])
    assert len(mentions) == 2


# ── apply_crosslinks ───────────────────────────────────────────────

def test_apply_crosslinks_wraps_first_mention_only():
    text = "clubs are defined by clubs in the clubs module."
    result = apply_crosslinks(text, known_slugs=["clubs"])
    assert result.count("[[clubs]]") == 1
    assert result.count("clubs") >= 3


def test_apply_crosslinks_ignores_already_linked():
    text = "[[clubs]] are organizations."
    result = apply_crosslinks(text, known_slugs=["clubs"])
    assert result == text


def test_apply_crosslinks_handles_multiple_slugs():
    text = "The clubs connect to the api-gateway."
    result = apply_crosslinks(text, known_slugs=["clubs", "api-gateway"])
    assert "[[clubs]]" in result
    assert "[[api-gateway]]" in result


def test_apply_crosslinks_preserves_original_case():
    text = "Clubs are great."
    result = apply_crosslinks(text, known_slugs=["clubs"])
    assert "[[clubs|Clubs]]" in result or "[[Clubs]]" in result


# ── find_dangling_refs ─────────────────────────────────────────────

def test_find_dangling_refs_detects_unknown_links():
    text = "See [[clubs]] and [[nonexistent]] for more."
    dangling = find_dangling_refs(text, known_slugs=["clubs", "api-gateway"])
    assert dangling == ["nonexistent"]


def test_find_dangling_refs_none_when_all_known():
    text = "See [[clubs]] and [[api-gateway]]."
    assert find_dangling_refs(text, known_slugs=["clubs", "api-gateway"]) == []


def test_find_dangling_refs_deduplicates():
    text = "[[ghost]] and [[ghost]] again."
    assert find_dangling_refs(text, known_slugs=[]) == ["ghost"]


# ── run_pass2 ──────────────────────────────────────────────────────

def test_run_pass2_updates_articles_and_reports(tmp_path):
    (tmp_path / "clubs.md").write_text(
        "---\ntitle: Clubs\n---\n\n# Clubs\n\nLinks to api-gateway.\n",
        encoding="utf-8",
    )
    (tmp_path / "api-gateway.md").write_text(
        "---\ntitle: API\n---\n\n# API Gateway\n\nSee clubs.\n",
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")

    report = run_pass2(
        wiki_dir=tmp_path,
        known_slugs=["clubs", "api-gateway"],
    )
    assert isinstance(report, Pass2Report)
    assert report.links_added == 2
    assert report.dangling == {}

    assert "[[api-gateway]]" in (tmp_path / "clubs.md").read_text()
    assert "[[clubs]]" in (tmp_path / "api-gateway.md").read_text()


def test_run_pass2_collects_dangling_per_article(tmp_path):
    (tmp_path / "clubs.md").write_text(
        "# Clubs\n\nSee [[ghost]] and [[missing]].\n",
        encoding="utf-8",
    )
    report = run_pass2(wiki_dir=tmp_path, known_slugs=["clubs"])
    assert "clubs" in report.dangling
    assert set(report.dangling["clubs"]) == {"ghost", "missing"}


def test_run_pass2_skips_index_and_log_files(tmp_path):
    (tmp_path / "index.md").write_text("# Index\n\nMention clubs.\n")
    (tmp_path / "log.md").write_text("# Log\n\n## [2026-04-14 22:15] ingest | clubs\n")

    report = run_pass2(wiki_dir=tmp_path, known_slugs=["clubs"])
    assert "[[clubs]]" not in (tmp_path / "index.md").read_text()
    assert "[[clubs]]" not in (tmp_path / "log.md").read_text()
    assert report.links_added == 0
