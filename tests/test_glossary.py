"""Tests for glossary.py — pinned context block for Pass 1 prompts."""
from pathlib import Path

import pytest

from glossary import build_glossary_block


def test_build_glossary_block_includes_overview_and_index(tmp_path):
    wiki = tmp_path
    (wiki / "overview.md").write_text("# Arena Overview\n\nBounded contexts...\n")
    (wiki / "index.md").write_text("# Index\n\n- [[clubs]]\n- [[api-gateway]]\n")

    block = build_glossary_block(wiki, known_slugs=["clubs", "api-gateway"])

    assert "Overview" in block
    assert "Bounded contexts" in block
    assert "Index" in block
    assert "[[clubs]]" in block
    assert "[[api-gateway]]" in block


def test_build_glossary_block_truncates_long_overview(tmp_path):
    wiki = tmp_path
    big = "word " * 10_000  # ~50KB
    (wiki / "overview.md").write_text(f"# Overview\n\n{big}")
    (wiki / "index.md").write_text("# Index\n")

    block = build_glossary_block(wiki, known_slugs=[])
    assert len(block) < 15_000


def test_build_glossary_block_lists_known_slugs_for_linking():
    block = build_glossary_block(wiki_dir=Path("/nonexistent"),
                                 known_slugs=["clubs", "api-gateway", "fan-entity"])
    assert "clubs" in block
    assert "api-gateway" in block
    assert "fan-entity" in block


def test_build_glossary_block_handles_missing_overview(tmp_path):
    block = build_glossary_block(tmp_path, known_slugs=["x"])
    assert "x" in block
