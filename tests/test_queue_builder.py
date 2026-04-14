"""Tests for queue_builder.py — end-to-end queue construction."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from provenance import Provenance, SourceRef, write_provenance
from queue_builder import QueueItem, build_queue
from ripple_map import RippleRule
from topo import ArticlePriority


@pytest.fixture
def wiki_with_provenance(tmp_path: Path) -> Path:
    """Simulate a wiki where two articles have provenance declared."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    (wiki / "clubs.md").write_text("# Clubs\n\nBody.\n", encoding="utf-8")
    write_provenance(wiki / "clubs.md", Provenance(
        sources=[SourceRef(path="services/clubs/entity.go", sha="abc")],
        compiled_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        model="sonnet-4-6", cost_usd=0.10, triggered_by="manual",
    ))

    (wiki / "api-gateway.md").write_text("# API Gateway\n\nBody.\n", encoding="utf-8")
    write_provenance(wiki / "api-gateway.md", Provenance(
        sources=[SourceRef(path="services/api-gateway/main.go", sha="xyz")],
        compiled_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        model="sonnet-4-6", cost_usd=0.18, triggered_by="manual",
    ))
    return wiki


@pytest.fixture
def manifest() -> list[ArticleSpec]:
    return [
        ArticleSpec("clubs", ArticlePriority.SERVICES, ["services/clubs/**"]),
        ArticleSpec("api-gateway", ArticlePriority.SERVICES, ["services/api-gateway/**"]),
        ArticleSpec("club-finance", ArticlePriority.SERVICES, ["services/club-finance/**"]),
        ArticleSpec("fan-entity", ArticlePriority.MODELS, ["proto/user/**"]),
    ]


def test_build_queue_from_single_file_change_via_reverse_index(
    wiki_with_provenance, manifest
):
    queue = build_queue(
        changed_files=["services/clubs/entity.go"],
        manifest=manifest,
        ripple_rules=[],
        wiki_dir=wiki_with_provenance,
        trigger="manual",
    )
    assert [item.slug for item in queue] == ["clubs"]
    assert queue[0].trigger == "manual"


def test_build_queue_uses_ripple_map_for_hidden_deps(
    wiki_with_provenance, manifest
):
    # services/clubs/entity.go is only declared by clubs article,
    # but ripple rule expands to api-gateway and club-finance too.
    ripple = [
        RippleRule(
            match="services/clubs/**",
            revalidate=["api-gateway", "club-finance"],
        ),
    ]
    queue = build_queue(
        changed_files=["services/clubs/entity.go"],
        manifest=manifest,
        ripple_rules=ripple,
        wiki_dir=wiki_with_provenance,
        trigger="manual",
    )
    slugs = [item.slug for item in queue]
    # Topological order: all SERVICES tier, alphabetical within tier
    assert slugs == ["api-gateway", "club-finance", "clubs"]


def test_build_queue_sorts_across_tiers(
    wiki_with_provenance, manifest
):
    ripple = [
        RippleRule(
            match="services/clubs/**",
            revalidate=["fan-entity", "api-gateway"],
        ),
    ]
    queue = build_queue(
        changed_files=["services/clubs/entity.go"],
        manifest=manifest,
        ripple_rules=ripple,
        wiki_dir=wiki_with_provenance,
        trigger="manual",
    )
    slugs = [item.slug for item in queue]
    # MODELS tier before SERVICES tier
    assert slugs[0] == "fan-entity"
    assert set(slugs[1:]) == {"api-gateway", "clubs"}


def test_build_queue_deduplicates_across_sources(
    wiki_with_provenance, manifest
):
    ripple = [
        RippleRule(match="services/clubs/**", revalidate=["clubs"]),
    ]
    queue = build_queue(
        changed_files=["services/clubs/entity.go"],
        manifest=manifest,
        ripple_rules=ripple,
        wiki_dir=wiki_with_provenance,
        trigger="manual",
    )
    # "clubs" appears via both reverse index and ripple rule,
    # but only once in queue.
    assert [item.slug for item in queue] == ["clubs"]


def test_build_queue_empty_when_no_changes_match(
    wiki_with_provenance, manifest
):
    queue = build_queue(
        changed_files=["README.md"],
        manifest=manifest,
        ripple_rules=[],
        wiki_dir=wiki_with_provenance,
        trigger="manual",
    )
    assert queue == []


def test_build_queue_item_carries_trigger(
    wiki_with_provenance, manifest
):
    queue = build_queue(
        changed_files=["services/clubs/entity.go"],
        manifest=manifest,
        ripple_rules=[],
        wiki_dir=wiki_with_provenance,
        trigger="commit:abc123",
    )
    assert queue[0].trigger == "commit:abc123"


def test_build_queue_finds_first_compile_via_manifest_globs(tmp_path, manifest):
    """Article has no provenance yet → must still be enqueued when its
    manifest sources glob matches a changed file (first-compile case)."""
    empty_wiki = tmp_path / "empty-wiki"
    empty_wiki.mkdir()

    queue = build_queue(
        changed_files=["services/club-finance/main.go"],
        manifest=manifest,
        ripple_rules=[],
        wiki_dir=empty_wiki,
        trigger="manual",
    )
    slugs = [item.slug for item in queue]
    assert "club-finance" in slugs
