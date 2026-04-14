"""Append-only chronological log for wiki operations.

Pattern from https://gist.github.com/snemesh/0f36e7e4dcb7e536b5238dbf3f9441b3
— markdown file with `## [YYYY-MM-DD HH:MM] type | summary | k=v | k=v` lines,
parseable via `grep "^## \\[" log.md | tail -N`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LogEntry:
    """Single log line's data."""
    ts: datetime
    event: str  # e.g. "ingest", "lint", "sync-start"
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


def format_entry(entry: LogEntry) -> str:
    """Render LogEntry as a single markdown line per llm-wiki spec."""
    ts_str = entry.ts.strftime("%Y-%m-%d %H:%M")
    parts = [f"## [{ts_str}] {entry.event} | {entry.summary}"]
    for key, value in entry.metadata.items():
        parts.append(f"{key}={value}")
    return " | ".join(parts)


_LOG_HEADER = (
    "# Wiki Operations Log\n\n"
    "Append-only, chronological. Grep-parseable:\n"
    "`grep '^## \\[' log.md | tail -20`\n\n"
)


def append_entry(log_file: Path, entry: LogEntry) -> None:
    """Append a LogEntry to log.md, creating the file with a header if needed.

    Atomic per-line append: open in append mode, write, close. No locking —
    multiple writers would interleave by line but never corrupt. Acceptable
    because we have a single sync process at a time.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if not log_file.exists():
        log_file.write_text(_LOG_HEADER, encoding="utf-8")
    line = format_entry(entry) + "\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line)
