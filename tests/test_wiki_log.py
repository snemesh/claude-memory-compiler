"""Tests for wiki_log.py — llm-wiki pattern append-only log."""
from datetime import datetime, timezone

import pytest

from wiki_log import LogEntry, append_entry, format_entry, tail_entries


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


def test_append_entry_creates_file_with_header(tmp_path):
    log_file = tmp_path / "log.md"
    entry = LogEntry(
        ts=datetime(2026, 4, 14, 22, 18, tzinfo=timezone.utc),
        event="ingest",
        summary="clubs",
    )
    append_entry(log_file, entry)

    content = log_file.read_text(encoding="utf-8")
    assert content.startswith("# Wiki Operations Log\n")
    assert "## [2026-04-14 22:18] ingest | clubs" in content


def test_append_entry_preserves_existing_content(tmp_path):
    log_file = tmp_path / "log.md"
    log_file.write_text("# Wiki Operations Log\n\n## [2026-04-10 10:00] ingest | foo\n",
                        encoding="utf-8")

    entry = LogEntry(
        ts=datetime(2026, 4, 14, 22, 18, tzinfo=timezone.utc),
        event="ingest",
        summary="bar",
    )
    append_entry(log_file, entry)

    content = log_file.read_text(encoding="utf-8")
    # Both entries present, in order
    assert content.index("foo") < content.index("bar")


def test_append_entry_grep_parseable(tmp_path):
    """Entries must be filterable with `grep '^## \\[' log.md`."""
    log_file = tmp_path / "log.md"
    for i in range(3):
        append_entry(log_file, LogEntry(
            ts=datetime(2026, 4, 14, 22, i, tzinfo=timezone.utc),
            event="ingest",
            summary=f"art{i}",
        ))

    lines = log_file.read_text(encoding="utf-8").splitlines()
    entry_lines = [line for line in lines if line.startswith("## [")]
    assert len(entry_lines) == 3


def test_tail_entries_returns_last_n(tmp_path):
    log_file = tmp_path / "log.md"
    for i in range(5):
        append_entry(log_file, LogEntry(
            ts=datetime(2026, 4, 14, 22, i, tzinfo=timezone.utc),
            event="ingest",
            summary=f"art{i}",
        ))

    entries = tail_entries(log_file, n=3)
    assert len(entries) == 3
    assert [e.summary for e in entries] == ["art2", "art3", "art4"]


def test_tail_entries_parses_metadata(tmp_path):
    log_file = tmp_path / "log.md"
    append_entry(log_file, LogEntry(
        ts=datetime(2026, 4, 14, 22, 18, tzinfo=timezone.utc),
        event="ingest",
        summary="clubs",
        metadata={"cost_usd": 0.12, "commit": "a7f2c9"},
    ))

    entries = tail_entries(log_file, n=5)
    assert len(entries) == 1
    assert entries[0].metadata == {"cost_usd": "0.12", "commit": "a7f2c9"}


def test_tail_entries_missing_file_returns_empty(tmp_path):
    assert tail_entries(tmp_path / "nonexistent.md", n=10) == []
