"""Tests for wiki_log.py — llm-wiki pattern append-only log."""
from datetime import datetime, timezone

import pytest

from wiki_log import LogEntry, format_entry


def test_format_entry_minimal():
    entry = LogEntry(
        ts=datetime(2026, 4, 14, 22, 18, tzinfo=timezone.utc),
        event="ingest",
        summary="clubs",
    )
    line = format_entry(entry)
    assert line == "## [2026-04-14 22:18] ingest | clubs"


def test_format_entry_with_metadata():
    entry = LogEntry(
        ts=datetime(2026, 4, 14, 22, 18, tzinfo=timezone.utc),
        event="ingest",
        summary="clubs",
        metadata={"cost_usd": 0.12, "sources": 8, "commit": "a7f2c9"},
    )
    line = format_entry(entry)
    assert line == "## [2026-04-14 22:18] ingest | clubs | cost_usd=0.12 | sources=8 | commit=a7f2c9"


def test_format_entry_preserves_metadata_order():
    entry = LogEntry(
        ts=datetime(2026, 4, 14, 22, 18, tzinfo=timezone.utc),
        event="sync-start",
        summary="47 articles queued",
        metadata={"est_cost_usd": 4.70, "budget_5h_pct": 72.0},
    )
    line = format_entry(entry)
    # Dict insertion order must be preserved
    assert "est_cost_usd=4.7 | budget_5h_pct=72.0" in line
