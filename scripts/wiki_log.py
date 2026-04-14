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
