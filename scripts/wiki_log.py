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


import re
from datetime import timezone

_ENTRY_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2})\] "
    r"(?P<event>[\w-]+) \| (?P<rest>.*)$"
)


def _parse_entry(line: str) -> LogEntry | None:
    """Parse a single log line back into a LogEntry. None if malformed."""
    match = _ENTRY_RE.match(line.rstrip())
    if not match:
        return None
    ts = datetime.strptime(
        f"{match['date']} {match['time']}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)

    segments = match["rest"].split(" | ")
    summary = segments[0]
    metadata: dict[str, Any] = {}
    for seg in segments[1:]:
        if "=" in seg:
            k, _, v = seg.partition("=")
            metadata[k.strip()] = v.strip()
    return LogEntry(ts=ts, event=match["event"], summary=summary, metadata=metadata)


def tail_entries(log_file: Path, n: int) -> list[LogEntry]:
    """Return the last N entries parsed from log.md. Empty list if no file."""
    if not log_file.exists():
        return []
    lines = log_file.read_text(encoding="utf-8").splitlines()
    parsed = [e for line in lines if (e := _parse_entry(line)) is not None]
    return parsed[-n:] if n > 0 else parsed
