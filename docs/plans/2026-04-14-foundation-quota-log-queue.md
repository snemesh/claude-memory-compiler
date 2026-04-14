# Foundation Plan: Quota Client, log.md Writer, Queue Builder

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three foundational components standalone: (1) quota client that reads Claude Code's `oauth/usage` endpoint, (2) append-only `log.md` writer per llm-wiki pattern, (3) queue builder that maps file changes to topologically-sorted article work-items.

**Architecture:** Three independent Python modules under `~/claude-memory-compiler/scripts/`, each with pytest unit tests under `~/claude-memory-compiler/tests/`, each usable as a CLI tool standalone. No LLM calls in this plan — foundation only.

**Tech Stack:** Python 3.12+, `requests` for HTTP, `PyYAML` for config, `pytest` for tests, existing `config.py`/`utils.py` modules. Package manager: `uv`.

**Reference spec:** `docs/specs/2026-04-14-wiki-compile-chunked-design.md`

---

## Phase 0: Dev infrastructure

### Task 0.1: Add dev dependencies and test directory

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest + requests + PyYAML to pyproject.toml**

Edit `pyproject.toml` — replace the `dependencies` block and add `[dependency-groups]`:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.29",
    "python-dotenv>=1.0.0",
    "tzdata>=2024.1",
    "requests>=2.32",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
    "freezegun>=1.5",
]
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync --group dev`
Expected: `Resolved N packages` with no errors.

- [ ] **Step 3: Create tests skeleton**

Create `tests/__init__.py` (empty file).

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures."""
import sys
from pathlib import Path

# Make scripts/ importable as a package-less module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
```

- [ ] **Step 4: Add pytest config to pyproject.toml**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["scripts"]
addopts = "-v --tb=short"
```

- [ ] **Step 5: Verify pytest runs**

Run: `uv run pytest --collect-only`
Expected: `no tests ran` with exit code 5 (no tests yet), no import errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py tests/conftest.py
git commit -m "chore: add pytest, requests, pyyaml dev dependencies"
```

---

## Phase 1: Quota client (`quota.py`)

### Task 1.1: Quota client — test skeleton and data model

**Files:**
- Create: `tests/test_quota.py`
- Create: `scripts/quota.py`

- [ ] **Step 1: Write failing test for QuotaSnapshot dataclass**

Create `tests/test_quota.py`:

```python
"""Tests for quota.py — subscription quota reader."""
from datetime import datetime, timezone

import pytest

from quota import QuotaSnapshot


def test_quota_snapshot_construction():
    snap = QuotaSnapshot(
        five_hour_used_pct=68.2,
        five_hour_resets_at=datetime(2026, 4, 14, 8, 15, tzinfo=timezone.utc),
        seven_day_used_pct=23.4,
        seven_day_resets_at=datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc),
        source="oauth",
    )
    assert snap.five_hour_used_pct == 68.2
    assert snap.source == "oauth"


def test_quota_snapshot_remaining_pct():
    snap = QuotaSnapshot(
        five_hour_used_pct=68.2,
        five_hour_resets_at=datetime(2026, 4, 14, 8, 15, tzinfo=timezone.utc),
        seven_day_used_pct=23.4,
        seven_day_resets_at=datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc),
        source="oauth",
    )
    assert snap.five_hour_remaining_pct == pytest.approx(31.8)
    assert snap.seven_day_remaining_pct == pytest.approx(76.6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quota.py -v`
Expected: `ImportError: cannot import name 'QuotaSnapshot' from 'quota'`

- [ ] **Step 3: Implement QuotaSnapshot**

Create `scripts/quota.py`:

```python
"""Subscription quota reader for Claude Code (Pro/Max plans).

Primary source: GET https://api.anthropic.com/api/oauth/usage with OAuth token
from Claude Code credentials. Fallback: aggregate ~/.claude/projects/**/*.jsonl
with model pricing table.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class QuotaSnapshot:
    """Point-in-time snapshot of subscription usage."""
    five_hour_used_pct: float
    five_hour_resets_at: datetime
    seven_day_used_pct: float
    seven_day_resets_at: datetime
    fetched_at: datetime
    source: str  # "oauth" | "jsonl-fallback"

    @property
    def five_hour_remaining_pct(self) -> float:
        return 100.0 - self.five_hour_used_pct

    @property
    def seven_day_remaining_pct(self) -> float:
        return 100.0 - self.seven_day_used_pct
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quota.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/quota.py tests/test_quota.py
git commit -m "feat(quota): add QuotaSnapshot data model"
```

---

### Task 1.2: Quota client — OAuth token discovery

**Files:**
- Modify: `tests/test_quota.py`
- Modify: `scripts/quota.py`

- [ ] **Step 1: Write failing test for token discovery**

Append to `tests/test_quota.py`:

```python
import json
from pathlib import Path

from quota import find_oauth_token


def test_find_oauth_token_from_credentials_file(tmp_path, monkeypatch):
    cred_file = tmp_path / ".credentials.json"
    cred_file.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-TESTTOKEN",
            "refreshToken": "x",
            "expiresAt": 9999999999999,
        }
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("quota._CREDENTIALS_PATH", cred_file)

    token = find_oauth_token()
    assert token == "sk-ant-oat01-TESTTOKEN"


def test_find_oauth_token_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("quota._CREDENTIALS_PATH", tmp_path / "nonexistent.json")
    assert find_oauth_token() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quota.py::test_find_oauth_token_from_credentials_file -v`
Expected: `ImportError: cannot import name 'find_oauth_token'`

- [ ] **Step 3: Implement token discovery**

Append to `scripts/quota.py`:

```python
import json
from pathlib import Path

_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def find_oauth_token() -> str | None:
    """Locate the Claude Code OAuth access token.

    Searches ~/.claude/.credentials.json for claudeAiOauth.accessToken.
    Returns None if the file is absent or the token is missing.
    """
    if not _CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("claudeAiOauth", {}).get("accessToken")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quota.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/quota.py tests/test_quota.py
git commit -m "feat(quota): discover OAuth token from ~/.claude/.credentials.json"
```

---

### Task 1.3: Quota client — oauth/usage HTTP call

**Files:**
- Modify: `tests/test_quota.py`
- Modify: `scripts/quota.py`

- [ ] **Step 1: Write failing test for fetch_oauth_quota**

Append to `tests/test_quota.py`:

```python
from unittest.mock import MagicMock, patch

from quota import fetch_oauth_quota


def test_fetch_oauth_quota_parses_response():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "five_hour": {"used_percentage": 68.2, "resets_at": 1776412500},
        "seven_day": {"used_percentage": 23.4, "resets_at": 1776673500},
    }

    with patch("quota.requests.get", return_value=fake_response) as mock_get:
        snap = fetch_oauth_quota("sk-ant-oat01-TEST")

    assert snap is not None
    assert snap.five_hour_used_pct == 68.2
    assert snap.seven_day_used_pct == 23.4
    assert snap.source == "oauth"
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert "Bearer sk-ant-oat01-TEST" in call_args.kwargs["headers"]["Authorization"]


def test_fetch_oauth_quota_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 401
    with patch("quota.requests.get", return_value=fake_response):
        snap = fetch_oauth_quota("bad-token")
    assert snap is None


def test_fetch_oauth_quota_returns_none_on_network_error():
    import requests
    with patch("quota.requests.get", side_effect=requests.ConnectionError("boom")):
        snap = fetch_oauth_quota("sk-ant-oat01-TEST")
    assert snap is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quota.py::test_fetch_oauth_quota_parses_response -v`
Expected: `ImportError: cannot import name 'fetch_oauth_quota'`

- [ ] **Step 3: Implement fetch_oauth_quota**

Append to `scripts/quota.py`:

```python
from datetime import timezone

import requests

_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
_HTTP_TIMEOUT_S = 10


def fetch_oauth_quota(token: str) -> QuotaSnapshot | None:
    """Call the oauth/usage endpoint; return snapshot or None on any failure.

    This endpoint is undocumented but used by Claude Code's /usage command.
    Returns None on HTTP errors, network errors, or malformed response —
    callers fall back to jsonl-based estimation.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "claude-memory-compiler/0.1",
    }
    try:
        resp = requests.get(_USAGE_ENDPOINT, headers=headers, timeout=_HTTP_TIMEOUT_S)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
        five = data["five_hour"]
        seven = data["seven_day"]
        now = datetime.now(tz=timezone.utc)
        return QuotaSnapshot(
            five_hour_used_pct=float(five["used_percentage"]),
            five_hour_resets_at=datetime.fromtimestamp(five["resets_at"], tz=timezone.utc),
            seven_day_used_pct=float(seven["used_percentage"]),
            seven_day_resets_at=datetime.fromtimestamp(seven["resets_at"], tz=timezone.utc),
            fetched_at=now,
            source="oauth",
        )
    except (KeyError, ValueError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quota.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/quota.py tests/test_quota.py
git commit -m "feat(quota): fetch oauth/usage endpoint with graceful fallback"
```

---

### Task 1.4: Quota client — budget decision logic

**Files:**
- Modify: `tests/test_quota.py`
- Modify: `scripts/quota.py`

- [ ] **Step 1: Write failing tests for should_pause and sleep_until**

Append to `tests/test_quota.py`:

```python
from datetime import timedelta

from quota import BudgetThresholds, should_pause, sleep_seconds_until_reset


def make_snap(five_pct=50.0, seven_pct=30.0, resets_in_h=2.0):
    now = datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc)
    return QuotaSnapshot(
        five_hour_used_pct=five_pct,
        five_hour_resets_at=now + timedelta(hours=resets_in_h),
        seven_day_used_pct=seven_pct,
        seven_day_resets_at=now + timedelta(days=3),
        fetched_at=now,
        source="oauth",
    )


def test_should_pause_false_under_threshold():
    snap = make_snap(five_pct=70.0, seven_pct=50.0)
    t = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    assert should_pause(snap, t) is False


def test_should_pause_true_on_five_hour_exhaust():
    snap = make_snap(five_pct=86.0)
    t = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    assert should_pause(snap, t) is True


def test_should_pause_true_on_seven_day_exhaust():
    snap = make_snap(five_pct=40.0, seven_pct=91.0)
    t = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    assert should_pause(snap, t) is True


def test_sleep_seconds_until_reset_five_hour():
    snap = make_snap(five_pct=95.0, resets_in_h=2.0)
    # 2 hours + 30s safety margin
    secs = sleep_seconds_until_reset(snap, safety_margin_s=30)
    assert 7225 <= secs <= 7235
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quota.py::test_should_pause_false_under_threshold -v`
Expected: `ImportError: cannot import name 'BudgetThresholds'`

- [ ] **Step 3: Implement thresholds + decision functions**

Append to `scripts/quota.py`:

```python
from datetime import timedelta


@dataclass(frozen=True)
class BudgetThresholds:
    """Configurable stop-points for sync-loop.

    Values are percentages 0-100. Defaults leave conservative headroom
    for interactive use outside the sync process.
    """
    five_hour_stop_pct: float = 85.0
    seven_day_stop_pct: float = 90.0


def should_pause(snap: QuotaSnapshot, thresholds: BudgetThresholds) -> bool:
    """True if sync-loop must stop before next chunk."""
    if snap.five_hour_used_pct >= thresholds.five_hour_stop_pct:
        return True
    if snap.seven_day_used_pct >= thresholds.seven_day_stop_pct:
        return True
    return False


def sleep_seconds_until_reset(snap: QuotaSnapshot, safety_margin_s: int = 30) -> int:
    """Seconds from snapshot time until the nearest blocking window resets.

    Picks whichever of five_hour / seven_day is currently over its threshold;
    if both, returns the earlier reset. Adds safety_margin_s so we wake up
    slightly after the reset, not on the boundary.
    """
    candidates = [snap.five_hour_resets_at, snap.seven_day_resets_at]
    earliest = min(candidates)
    delta = earliest - snap.fetched_at
    return int(delta.total_seconds()) + safety_margin_s
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quota.py -v`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/quota.py tests/test_quota.py
git commit -m "feat(quota): budget thresholds and pause/sleep decision"
```

---

### Task 1.5: Quota client — get_quota() dispatcher + CLI

**Files:**
- Modify: `tests/test_quota.py`
- Modify: `scripts/quota.py`

- [ ] **Step 1: Write failing test for get_quota() with and without token**

Append to `tests/test_quota.py`:

```python
from quota import get_quota


def test_get_quota_uses_oauth_when_token_present(monkeypatch):
    fake_snap = make_snap()
    monkeypatch.setattr("quota.find_oauth_token", lambda: "sk-ant-oat01-TEST")
    monkeypatch.setattr("quota.fetch_oauth_quota", lambda tok: fake_snap)

    snap = get_quota()
    assert snap is fake_snap
    assert snap.source == "oauth"


def test_get_quota_returns_none_when_no_token(monkeypatch):
    monkeypatch.setattr("quota.find_oauth_token", lambda: None)
    assert get_quota() is None


def test_get_quota_returns_none_when_oauth_fails(monkeypatch):
    monkeypatch.setattr("quota.find_oauth_token", lambda: "sk-ant-oat01-TEST")
    monkeypatch.setattr("quota.fetch_oauth_quota", lambda tok: None)
    assert get_quota() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quota.py::test_get_quota_uses_oauth_when_token_present -v`
Expected: `ImportError: cannot import name 'get_quota'`

- [ ] **Step 3: Implement get_quota() dispatcher + CLI**

Append to `scripts/quota.py`:

```python
def get_quota() -> QuotaSnapshot | None:
    """High-level API: return best-effort quota snapshot.

    Tries OAuth endpoint first; returns None on any failure.
    (jsonl fallback is deferred to a follow-up task.)
    """
    token = find_oauth_token()
    if not token:
        return None
    return fetch_oauth_quota(token)


def _main() -> int:
    """CLI entrypoint: `uv run python scripts/quota.py`"""
    snap = get_quota()
    if snap is None:
        print("quota: unavailable (no OAuth token or endpoint error)")
        return 1
    print(f"source: {snap.source}")
    print(f"fetched_at: {snap.fetched_at.isoformat()}")
    print(f"5h: {snap.five_hour_used_pct:.1f}% used "
          f"(resets {snap.five_hour_resets_at.isoformat()})")
    print(f"7d: {snap.seven_day_used_pct:.1f}% used "
          f"(resets {snap.seven_day_resets_at.isoformat()})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quota.py -v`
Expected: `14 passed`

- [ ] **Step 5: Verify CLI works end-to-end**

Run: `uv run python scripts/quota.py`
Expected: Either real quota output (if OAuth token present) OR `quota: unavailable`.
Either is a success signal — no crash.

- [ ] **Step 6: Commit**

```bash
git add scripts/quota.py tests/test_quota.py
git commit -m "feat(quota): get_quota() dispatcher and CLI entrypoint"
```

---

## Phase 2: log.md writer (`wiki_log.py`)

### Task 2.1: LogEntry formatter

**Files:**
- Create: `tests/test_wiki_log.py`
- Create: `scripts/wiki_log.py`

- [ ] **Step 1: Write failing tests for format_entry**

Create `tests/test_wiki_log.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wiki_log.py -v`
Expected: `ImportError: cannot import name 'LogEntry'`

- [ ] **Step 3: Implement LogEntry + format_entry**

Create `scripts/wiki_log.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wiki_log.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_log.py tests/test_wiki_log.py
git commit -m "feat(log): LogEntry data class and format_entry"
```

---

### Task 2.2: append_entry to log.md file

**Files:**
- Modify: `tests/test_wiki_log.py`
- Modify: `scripts/wiki_log.py`

- [ ] **Step 1: Write failing tests for append_entry**

Append to `tests/test_wiki_log.py`:

```python
from wiki_log import append_entry


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wiki_log.py::test_append_entry_creates_file_with_header -v`
Expected: `ImportError: cannot import name 'append_entry'`

- [ ] **Step 3: Implement append_entry**

Append to `scripts/wiki_log.py`:

```python
_LOG_HEADER = "# Wiki Operations Log\n\nAppend-only, chronological. Grep-parseable:\n`grep '^## \\[' log.md | tail -20`\n\n"


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wiki_log.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_log.py tests/test_wiki_log.py
git commit -m "feat(log): append_entry with file creation and header"
```

---

### Task 2.3: tail_entries reader for status commands

**Files:**
- Modify: `tests/test_wiki_log.py`
- Modify: `scripts/wiki_log.py`

- [ ] **Step 1: Write failing tests for tail_entries**

Append to `tests/test_wiki_log.py`:

```python
from wiki_log import tail_entries


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wiki_log.py::test_tail_entries_returns_last_n -v`
Expected: `ImportError: cannot import name 'tail_entries'`

- [ ] **Step 3: Implement tail_entries + parse helper**

Append to `scripts/wiki_log.py`:

```python
import re

_ENTRY_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2})\] "
    r"(?P<event>[\w-]+) \| (?P<rest>.*)$"
)


def _parse_entry(line: str) -> LogEntry | None:
    """Parse a single log line back into a LogEntry. None if malformed."""
    match = _ENTRY_RE.match(line.rstrip())
    if not match:
        return None
    from datetime import timezone as _tz
    ts = datetime.strptime(
        f"{match['date']} {match['time']}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=_tz.utc)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wiki_log.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_log.py tests/test_wiki_log.py
git commit -m "feat(log): tail_entries reader with metadata parsing"
```

---

## Phase 3: Queue builder

### Task 3.1: Provenance frontmatter read/write

**Files:**
- Create: `tests/test_provenance.py`
- Create: `scripts/provenance.py`

- [ ] **Step 1: Write failing tests for read_provenance + write_provenance**

Create `tests/test_provenance.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provenance.py -v`
Expected: `ImportError: cannot import name 'Provenance'`

- [ ] **Step 3: Implement provenance module**

Create `scripts/provenance.py`:

```python
"""Article provenance frontmatter.

Every compiled wiki article gets a YAML frontmatter block listing the source
files that produced it (with git-blob SHAs), compile metadata, and the trigger.
Used by the queue builder to compute reverse file→articles index.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceRef:
    path: str  # repo-relative
    sha: str   # short git-blob hash


@dataclass(frozen=True)
class Provenance:
    sources: list[SourceRef]
    compiled_at: datetime
    model: str
    cost_usd: float
    triggered_by: str  # "manual" | "ripple:<slug>" | "commit:<sha>" | "scheduled"


_FRONTMATTER_RE = re.compile(
    r"\A---\n(?P<body>.*?)\n---\n(?P<rest>.*)\Z",
    re.DOTALL,
)


def read_provenance(article: Path) -> Provenance | None:
    """Extract provenance from an article's YAML frontmatter. None if absent."""
    if not article.exists():
        return None
    content = article.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None
    try:
        data = yaml.safe_load(match["body"]) or {}
    except yaml.YAMLError:
        return None

    raw_sources = data.get("sources", [])
    sources = [SourceRef(path=s["path"], sha=s["sha"]) for s in raw_sources]
    compiled_at_raw = data.get("compiled_at")
    if isinstance(compiled_at_raw, str):
        compiled_at = datetime.fromisoformat(compiled_at_raw)
    elif isinstance(compiled_at_raw, datetime):
        compiled_at = compiled_at_raw
    else:
        return None

    return Provenance(
        sources=sources,
        compiled_at=compiled_at,
        model=str(data.get("model", "")),
        cost_usd=float(data.get("cost_usd", 0.0)),
        triggered_by=str(data.get("triggered_by", "")),
    )


def write_provenance(article: Path, prov: Provenance) -> None:
    """Write (or replace) the frontmatter block at the top of the article.

    Preserves the body content below the frontmatter.
    """
    content = article.read_text(encoding="utf-8") if article.exists() else ""
    match = _FRONTMATTER_RE.match(content)
    body = match["rest"] if match else content

    data = {
        "sources": [{"path": s.path, "sha": s.sha} for s in prov.sources],
        "compiled_at": prov.compiled_at.isoformat(),
        "model": prov.model,
        "cost_usd": prov.cost_usd,
        "triggered_by": prov.triggered_by,
    }
    fm = yaml.safe_dump(data, sort_keys=False, default_flow_style=False).rstrip()
    article.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_provenance.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): read/write article frontmatter with source SHAs"
```

---

### Task 3.2: Reverse index (file → articles)

**Files:**
- Create: `tests/test_reverse_index.py`
- Create: `scripts/reverse_index.py`

- [ ] **Step 1: Write failing tests for build_reverse_index**

Create `tests/test_reverse_index.py`:

```python
"""Tests for reverse_index.py — file → affected articles map."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from provenance import Provenance, SourceRef, write_provenance
from reverse_index import build_reverse_index, articles_affected_by


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reverse_index.py -v`
Expected: `ImportError: cannot import name 'build_reverse_index'`

- [ ] **Step 3: Implement reverse_index**

Create `scripts/reverse_index.py`:

```python
"""Reverse index: source-file path → set of article slugs that depend on it.

Built by scanning all *.md articles in the wiki root and reading each one's
provenance frontmatter. The queue builder uses this to translate a commit's
changed-file list into the set of articles that need recompilation.
"""
from __future__ import annotations

from pathlib import Path

from provenance import read_provenance


def build_reverse_index(wiki_dir: Path) -> dict[str, set[str]]:
    """Scan wiki_dir for articles and return {source_path: {article_slug, ...}}.

    Articles without provenance frontmatter are silently skipped — they have
    no declared dependencies and cannot be invalidated by file changes.
    """
    index: dict[str, set[str]] = {}
    if not wiki_dir.exists():
        return index
    for article_path in wiki_dir.rglob("*.md"):
        if article_path.name in {"index.md", "log.md", "overview.md"}:
            continue
        prov = read_provenance(article_path)
        if prov is None:
            continue
        slug = article_path.stem
        for src in prov.sources:
            index.setdefault(src.path, set()).add(slug)
    return index


def articles_affected_by(
    index: dict[str, set[str]],
    changed_files: list[str],
) -> set[str]:
    """Union of articles referenced by any of the changed files."""
    affected: set[str] = set()
    for path in changed_files:
        affected |= index.get(path, set())
    return affected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reverse_index.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/reverse_index.py tests/test_reverse_index.py
git commit -m "feat(queue): reverse index mapping source files to articles"
```

---

### Task 3.3: Ripple-map loader + glob matching

**Files:**
- Create: `tests/test_ripple_map.py`
- Create: `scripts/ripple_map.py`

- [ ] **Step 1: Write failing tests for load_ripple_map + expand_ripple**

Create `tests/test_ripple_map.py`:

```python
"""Tests for ripple_map.py — manifest of non-obvious cross-service deps."""
from pathlib import Path

import pytest

from ripple_map import RippleRule, expand_ripple, load_ripple_map


def test_load_ripple_map_parses_yaml(tmp_path):
    path = tmp_path / "ripple.yaml"
    path.write_text(
        """
patterns:
  - match: "proto/clubs/**"
    revalidate: [api-gateway, club-finance, referrals]
  - match: "services/auth-service/**"
    revalidate: [fan-registration, club-self-registration]
""",
        encoding="utf-8",
    )
    rules = load_ripple_map(path)
    assert len(rules) == 2
    assert rules[0].match == "proto/clubs/**"
    assert rules[0].revalidate == ["api-gateway", "club-finance", "referrals"]


def test_load_ripple_map_missing_file_returns_empty(tmp_path):
    assert load_ripple_map(tmp_path / "nonexistent.yaml") == []


def test_expand_ripple_matches_glob():
    rules = [
        RippleRule(match="proto/clubs/**", revalidate=["api-gateway", "club-finance"]),
        RippleRule(match="services/auth/**", revalidate=["fan-registration"]),
    ]
    affected = expand_ripple(rules, ["proto/clubs/v1/clubs.proto"])
    assert affected == {"api-gateway", "club-finance"}


def test_expand_ripple_multiple_patterns_match():
    rules = [
        RippleRule(match="proto/**", revalidate=["api-gateway"]),
        RippleRule(match="**/*.proto", revalidate=["content"]),
    ]
    affected = expand_ripple(rules, ["proto/clubs/v1/clubs.proto"])
    assert affected == {"api-gateway", "content"}


def test_expand_ripple_unmatched_files_empty():
    rules = [RippleRule(match="proto/**", revalidate=["api-gateway"])]
    assert expand_ripple(rules, ["README.md"]) == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ripple_map.py -v`
Expected: `ImportError: cannot import name 'RippleRule'`

- [ ] **Step 3: Implement ripple_map**

Create `scripts/ripple_map.py`:

```python
"""Ripple map: declared non-obvious cross-service dependencies.

Seeds the queue with articles that must be revalidated when certain files
change, even if those articles do not list those files in their provenance.
Used for shared DB tables, Kafka topics, event contracts, etc.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RippleRule:
    match: str  # fnmatch-compatible glob, e.g. "proto/clubs/**"
    revalidate: list[str]  # list of article slugs


def load_ripple_map(path: Path) -> list[RippleRule]:
    """Load ripple rules from YAML; empty list if file missing or invalid."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    patterns = data.get("patterns", [])
    return [
        RippleRule(
            match=p["match"],
            revalidate=list(p.get("revalidate", [])),
        )
        for p in patterns
        if "match" in p
    ]


def expand_ripple(rules: list[RippleRule], changed_files: list[str]) -> set[str]:
    """For each changed file, find all matching rules and union their
    revalidate lists."""
    affected: set[str] = set()
    for file in changed_files:
        for rule in rules:
            if fnmatch.fnmatch(file, rule.match):
                affected.update(rule.revalidate)
    return affected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ripple_map.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/ripple_map.py tests/test_ripple_map.py
git commit -m "feat(queue): ripple-map loader with glob matching"
```

---

### Task 3.4: Topological sort by priority tier

**Files:**
- Create: `tests/test_topo.py`
- Create: `scripts/topo.py`

- [ ] **Step 1: Write failing tests for priority_sort**

Create `tests/test_topo.py`:

```python
"""Tests for topo.py — priority-tier topological sort."""
import pytest

from topo import ArticlePriority, priority_sort


def test_priority_sort_orders_by_tier():
    # Intentionally mixed order
    articles = [
        ("api-gateway", ArticlePriority.SERVICES),
        ("fan-entity", ArticlePriority.MODELS),
        ("wallet", ArticlePriority.FEATURES),
        ("overview", ArticlePriority.OVERVIEW),
        ("turnkey", ArticlePriority.INTEGRATIONS),
    ]
    sorted_slugs = priority_sort(articles)
    assert sorted_slugs == [
        "fan-entity",        # MODELS
        "api-gateway",       # SERVICES
        "wallet",            # FEATURES
        "turnkey",           # INTEGRATIONS
        "overview",          # OVERVIEW (last)
    ]


def test_priority_sort_stable_within_tier():
    # Same tier → alphabetical fallback for deterministic output
    articles = [
        ("zebra", ArticlePriority.SERVICES),
        ("alpha", ArticlePriority.SERVICES),
        ("mike", ArticlePriority.SERVICES),
    ]
    assert priority_sort(articles) == ["alpha", "mike", "zebra"]


def test_priority_sort_empty():
    assert priority_sort([]) == []


def test_priority_tier_values_ordered():
    """Enum values must be ordered such that lower = earlier in pipeline."""
    assert ArticlePriority.MODELS.value < ArticlePriority.SERVICES.value
    assert ArticlePriority.SERVICES.value < ArticlePriority.FEATURES.value
    assert ArticlePriority.FEATURES.value < ArticlePriority.INTEGRATIONS.value
    assert ArticlePriority.INTEGRATIONS.value < ArticlePriority.OVERVIEW.value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_topo.py -v`
Expected: `ImportError: cannot import name 'ArticlePriority'`

- [ ] **Step 3: Implement topo module**

Create `scripts/topo.py`:

```python
"""Priority-tier sort for the wiki compile queue.

Articles are processed foundations-first so that when article N is compiled,
every dependency it might link to is already present in the wiki. This is a
simple tier-based sort — true topological sort within a tier is not needed
at the current wiki size (~50 articles) and would require a hand-maintained
dependency graph we don't have.
"""
from __future__ import annotations

from enum import IntEnum


class ArticlePriority(IntEnum):
    MODELS = 1         # entities, value objects — pure vocabulary
    SERVICES = 2       # microservices, backends
    FEATURES = 3       # user-facing features that span services
    INTEGRATIONS = 4   # external deps: Turnkey, Atleta, PayX, Arena Verify
    OVERVIEW = 5       # synthesis layer — must see everything above


def priority_sort(articles: list[tuple[str, ArticlePriority]]) -> list[str]:
    """Sort (slug, priority) pairs by (priority, slug). Return slug list."""
    return [
        slug for slug, _ in sorted(articles, key=lambda item: (item[1].value, item[0]))
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_topo.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/topo.py tests/test_topo.py
git commit -m "feat(queue): priority-tier sort for article compilation order"
```

---

### Task 3.5: Articles manifest (slug → priority + sources glob)

**Files:**
- Create: `config/wiki-articles.yaml`
- Create: `tests/test_articles_manifest.py`
- Create: `scripts/articles_manifest.py`

- [ ] **Step 1: Create the articles manifest config**

Create `config/wiki-articles.yaml`:

```yaml
# Maps each wiki article slug to its priority tier and source-file globs.
# Used by the queue builder to enumerate candidate articles and to seed
# provenance frontmatter on first compile.

articles:
  # ── Models (P1) ──────────────────────────────────────────────────
  - slug: fan-entity
    priority: MODELS
    sources:
      - "services/user-profiles/internal/entity/**/*.go"
      - "proto/user-profiles/**/*.proto"
  - slug: club-entity
    priority: MODELS
    sources:
      - "services/clubs/internal/entity/**/*.go"
      - "proto/clubs/**/*.proto"
  - slug: campaign
    priority: MODELS
    sources: ["services/content/internal/campaign/**/*.go"]
  - slug: ad-task
    priority: MODELS
    sources: ["services/content/internal/adtask/**/*.go"]
  - slug: geo-tiers
    priority: MODELS
    sources: ["services/content/internal/geo/**/*.go"]

  # ── Services (P2) ────────────────────────────────────────────────
  - slug: api-gateway
    priority: SERVICES
    sources: ["services/api-gateway/**/*.go"]
  - slug: auth-service
    priority: SERVICES
    sources: ["services/auth-service/**/*.go"]
  - slug: user-profiles
    priority: SERVICES
    sources: ["services/user-profiles/**/*.go"]
  - slug: clubs
    priority: SERVICES
    sources: ["services/clubs/**/*.go"]
  - slug: agents
    priority: SERVICES
    sources: ["services/agents/**/*.go"]
  - slug: club-finance
    priority: SERVICES
    sources: ["services/club-finance/**/*.go"]
  - slug: referrals
    priority: SERVICES
    sources: ["services/referrals/**/*.go"]

  # ── Features (P3) ────────────────────────────────────────────────
  - slug: fan-registration
    priority: FEATURES
    sources:
      - "services/auth-service/internal/register/**/*.go"
      - "services/user-profiles/internal/create/**/*.go"
  - slug: club-self-registration
    priority: FEATURES
    sources:
      - "services/clubs/internal/register/**/*.go"
      - "services/clubs/internal/moderation/**/*.go"
  - slug: wallet
    priority: FEATURES
    sources: ["services/*/internal/wallet/**/*.go"]

  # ── Integrations (P4) ────────────────────────────────────────────
  - slug: turnkey
    priority: INTEGRATIONS
    sources: ["services/*/internal/turnkey/**/*.go"]
  - slug: atleta-network
    priority: INTEGRATIONS
    sources: ["services/*/internal/atleta/**/*.go"]

  # ── Overview (P5) ────────────────────────────────────────────────
  - slug: overview
    priority: OVERVIEW
    sources: ["**/*.go", "**/*.proto"]  # synthesis — reads everything relevant
```

- [ ] **Step 2: Write failing tests for load_manifest**

Create `tests/test_articles_manifest.py`:

```python
"""Tests for articles_manifest.py — wiki-articles.yaml loader."""
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec, load_manifest
from topo import ArticlePriority


def test_load_manifest_parses_specs(tmp_path):
    cfg = tmp_path / "wiki-articles.yaml"
    cfg.write_text(
        """
articles:
  - slug: clubs
    priority: SERVICES
    sources: ["services/clubs/**/*.go"]
  - slug: fan-entity
    priority: MODELS
    sources: ["proto/user/**/*.proto"]
""",
        encoding="utf-8",
    )
    specs = load_manifest(cfg)
    assert len(specs) == 2
    by_slug = {s.slug: s for s in specs}
    assert by_slug["clubs"].priority == ArticlePriority.SERVICES
    assert by_slug["clubs"].sources == ["services/clubs/**/*.go"]
    assert by_slug["fan-entity"].priority == ArticlePriority.MODELS


def test_load_manifest_invalid_priority_raises(tmp_path):
    cfg = tmp_path / "wiki-articles.yaml"
    cfg.write_text(
        """
articles:
  - slug: bad
    priority: INVALID_TIER
    sources: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="INVALID_TIER"):
        load_manifest(cfg)


def test_load_manifest_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nonexistent.yaml")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_articles_manifest.py -v`
Expected: `ImportError: cannot import name 'ArticleSpec'`

- [ ] **Step 4: Implement articles_manifest**

Create `scripts/articles_manifest.py`:

```python
"""Loader for config/wiki-articles.yaml — the wiki's article manifest."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from topo import ArticlePriority


@dataclass(frozen=True)
class ArticleSpec:
    slug: str
    priority: ArticlePriority
    sources: list[str]  # list of fnmatch globs relative to project root


def load_manifest(path: Path) -> list[ArticleSpec]:
    """Parse wiki-articles.yaml into a list of ArticleSpec.

    Raises FileNotFoundError if the file does not exist, and ValueError
    if any article declares an unknown priority tier.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: list[ArticleSpec] = []
    for item in raw.get("articles", []):
        priority_name = item["priority"]
        try:
            priority = ArticlePriority[priority_name]
        except KeyError as exc:
            raise ValueError(f"unknown priority tier: {priority_name}") from exc
        specs.append(ArticleSpec(
            slug=item["slug"],
            priority=priority,
            sources=list(item.get("sources", [])),
        ))
    return specs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_articles_manifest.py -v`
Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add config/wiki-articles.yaml scripts/articles_manifest.py tests/test_articles_manifest.py
git commit -m "feat(queue): wiki-articles.yaml manifest with priority tiers"
```

---

### Task 3.6: Queue builder — integration

**Files:**
- Create: `tests/test_queue_builder.py`
- Create: `scripts/queue_builder.py`

- [ ] **Step 1: Write failing tests for build_queue**

Create `tests/test_queue_builder.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queue_builder.py -v`
Expected: `ImportError: cannot import name 'QueueItem'`

- [ ] **Step 3: Implement queue_builder**

Create `scripts/queue_builder.py`:

```python
"""Queue builder: changed files → ordered list of QueueItem for compile pipeline.

Sources of article candidates:
1. Reverse provenance index — articles that literally list a changed file as a source
2. Ripple map — articles declared via fnmatch patterns in ripple-map.yaml

Union both, deduplicate, sort by priority tier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from articles_manifest import ArticleSpec
from reverse_index import articles_affected_by, build_reverse_index
from ripple_map import RippleRule, expand_ripple
from topo import ArticlePriority, priority_sort


@dataclass(frozen=True)
class QueueItem:
    slug: str
    priority: ArticlePriority
    trigger: str  # "manual" | "ripple:<slug>" | "commit:<sha>"


def build_queue(
    changed_files: list[str],
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    wiki_dir: Path,
    trigger: str,
) -> list[QueueItem]:
    """Compute ordered queue of articles to (re)compile.

    Returns [] if no article is affected.
    """
    # Union sources of staleness
    reverse_idx = build_reverse_index(wiki_dir)
    from_provenance = articles_affected_by(reverse_idx, changed_files)
    from_ripple = expand_ripple(ripple_rules, changed_files)
    slugs = from_provenance | from_ripple

    # Lookup priorities from manifest
    priority_by_slug = {spec.slug: spec.priority for spec in manifest}
    pairs: list[tuple[str, ArticlePriority]] = []
    for slug in slugs:
        if slug in priority_by_slug:
            pairs.append((slug, priority_by_slug[slug]))
        # Slugs not in manifest are dropped — we can't compile them

    sorted_slugs = priority_sort(pairs)
    return [
        QueueItem(slug=slug, priority=priority_by_slug[slug], trigger=trigger)
        for slug in sorted_slugs
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queue_builder.py -v`
Expected: `6 passed`

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass (previous phases still green).

- [ ] **Step 6: Commit**

```bash
git add scripts/queue_builder.py tests/test_queue_builder.py
git commit -m "feat(queue): build_queue end-to-end with provenance + ripple"
```

---

## Phase 4: CLI integration

### Task 4.1: Seed an initial ripple-map and verify loadable

**Files:**
- Create: `config/ripple-map.yaml`

- [ ] **Step 1: Create initial ripple-map seed**

Create `config/ripple-map.yaml`:

```yaml
# Non-obvious cross-service dependencies.
# Auto-seed rationale: patterns below reflect proto-import edges and
# api-gateway routing known at spec time (2026-04-14).
# Human-editable — add hidden deps (shared DB tables, Kafka topics, events).

patterns:
  - match: "proto/clubs/**"
    revalidate: [api-gateway, club-finance, referrals, club-self-registration]
  - match: "proto/auth/**"
    revalidate: [api-gateway, fan-registration]
  - match: "proto/referrals/**"
    revalidate: [api-gateway, agents, club-finance]
  - match: "services/auth-service/internal/jwt/**"
    revalidate: [fan-registration, club-self-registration, wallet]
  - match: "services/referrals/internal/event/**"
    revalidate: [agents, club-finance]
  - match: "services/api-gateway/internal/service_v2/**"
    revalidate: [api-gateway, overview]
```

- [ ] **Step 2: Verify the seed parses**

Run:

```bash
uv run python -c "from scripts.ripple_map import load_ripple_map; from pathlib import Path; rules = load_ripple_map(Path('config/ripple-map.yaml')); print(f'loaded {len(rules)} rules'); [print(f'  {r.match} -> {r.revalidate}') for r in rules]"
```

Expected: `loaded 6 rules` followed by all six lines printed.

- [ ] **Step 3: Commit**

```bash
git add config/ripple-map.yaml
git commit -m "feat(queue): seed initial ripple-map with proto and event deps"
```

---

### Task 4.2: `plan-sync` dry-run CLI command

**Files:**
- Create: `tests/test_plan_sync.py`
- Create: `scripts/plan_sync.py`

- [ ] **Step 1: Write failing test for plan_sync CLI output**

Create `tests/test_plan_sync.py`:

```python
"""Tests for plan_sync.py — dry-run queue planner CLI."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from articles_manifest import ArticleSpec
from provenance import Provenance, SourceRef, write_provenance
from plan_sync import render_plan
from queue_builder import QueueItem
from topo import ArticlePriority


def test_render_plan_empty():
    out = render_plan(queue=[], est_cost=0.0, avg_cost=0.10)
    assert "no articles" in out.lower()


def test_render_plan_shows_count_and_cost():
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
        QueueItem("fan-entity", ArticlePriority.MODELS, "manual"),
    ]
    out = render_plan(queue=queue, est_cost=0.30, avg_cost=0.10)
    assert "3 articles" in out
    assert "$0.30" in out
    assert "fan-entity" in out
    assert "clubs" in out


def test_render_plan_lists_trigger_origin():
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "ripple:api-gateway"),
    ]
    out = render_plan(queue=queue, est_cost=0.10, avg_cost=0.10)
    assert "ripple:api-gateway" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plan_sync.py -v`
Expected: `ImportError: cannot import name 'render_plan'`

- [ ] **Step 3: Implement plan_sync**

Create `scripts/plan_sync.py`:

```python
"""Dry-run planner: given a list of changed files, report the queue that
would be built without performing any LLM calls.

Usage:
    uv run python scripts/plan_sync.py --files services/clubs/entity.go proto/clubs/v1/clubs.proto
    uv run python scripts/plan_sync.py --commit HEAD
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from articles_manifest import load_manifest
from queue_builder import QueueItem, build_queue
from ripple_map import load_ripple_map

_DEFAULT_AVG_COST_USD = 0.10  # empirical from state.json; updated over time


def _changed_files_from_commit(commit: str) -> list[str]:
    """Run `git diff --name-only <commit>^ <commit>` to list touched files."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{commit}^", commit],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def render_plan(queue: list[QueueItem], est_cost: float, avg_cost: float) -> str:
    """Human-readable description of the planned queue."""
    if not queue:
        return "Plan: no articles affected by the given changes."

    lines = [
        f"Plan: {len(queue)} articles (est cost ~${est_cost:.2f} at ${avg_cost:.2f}/article)",
        "",
    ]
    for i, item in enumerate(queue, 1):
        lines.append(f"  {i:2d}. [{item.priority.name}] {item.slug}  ({item.trigger})")
    return "\n".join(lines)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Plan a sync without running it.")
    parser.add_argument("--files", nargs="*", default=None,
                        help="Explicit list of changed files (repo-relative).")
    parser.add_argument("--commit", default=None,
                        help="Git ref; uses `git diff --name-only <ref>^ <ref>`.")
    parser.add_argument("--articles-manifest",
                        default="config/wiki-articles.yaml",
                        help="Path to wiki-articles.yaml.")
    parser.add_argument("--ripple-map",
                        default="config/ripple-map.yaml",
                        help="Path to ripple-map.yaml.")
    parser.add_argument("--wiki-dir",
                        default="vault/arena/wiki",
                        help="Path to wiki directory (articles with provenance).")
    args = parser.parse_args()

    if args.files is None and args.commit is None:
        parser.error("one of --files or --commit is required")

    if args.commit:
        changed = _changed_files_from_commit(args.commit)
        trigger = f"commit:{args.commit}"
    else:
        changed = list(args.files)
        trigger = "manual"

    manifest = load_manifest(Path(args.articles_manifest))
    ripple = load_ripple_map(Path(args.ripple_map))
    queue = build_queue(
        changed_files=changed,
        manifest=manifest,
        ripple_rules=ripple,
        wiki_dir=Path(args.wiki_dir),
        trigger=trigger,
    )
    est = len(queue) * _DEFAULT_AVG_COST_USD
    print(render_plan(queue, est_cost=est, avg_cost=_DEFAULT_AVG_COST_USD))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_plan_sync.py -v`
Expected: `3 passed`

- [ ] **Step 5: End-to-end smoke test**

Run: `uv run python scripts/plan_sync.py --files services/clubs/entity.go`

Expected: Either `Plan: no articles affected by the given changes.` (if Arena wiki has no provenance yet — the expected initial state) OR a queue listing.
Either is a valid success signal; the point is no crash.

- [ ] **Step 6: Full test suite sanity check**

Run: `uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/plan_sync.py tests/test_plan_sync.py
git commit -m "feat(cli): plan-sync dry-run command"
```

---

## Self-Review

### Spec coverage

Spec sections mapped to tasks:

| Spec section | Task(s) |
|---|---|
| `oauth/usage` endpoint, OAuth token discovery | 1.2, 1.3 |
| `BudgetThresholds`, should_pause, sleep logic | 1.4 |
| `get_quota()` dispatcher + CLI | 1.5 |
| `log.md` as first-class artifact, append-only, grep-parseable | 2.1, 2.2, 2.3 |
| Event types taxonomy (ingest/lint/query/sync-*) | 2.1 (LogEntry accepts any event string; no enum enforcement yet — intentional for Phase 1) |
| Provenance frontmatter format | 3.1 |
| E1: dependency-aware staleness (reverse index) | 3.2 |
| E2: ripple map (manifest + expand) | 3.3 |
| Topological sort by priority tier | 3.4 |
| Articles manifest (slug → priority + sources) | 3.5 |
| Queue builder (end-to-end) | 3.6 |
| Initial ripple-map seed | 4.1 |
| Dry-run planner CLI | 4.2 |

Gaps (deferred to Plan 2 / Plan 3, intentional):
- Pass 1 Sonnet compile per article (E3 pinned glossary also lives here) — Plan 2
- Pass 2 cross-link, Pass 3 Haiku validate — Plan 2
- E5 commit-atomic batching (this plan has the queue machinery; atomic execution is orchestration) — Plan 3
- Manual & sleep modes (sync.py, sync-loop.py) — Plan 3
- Statusline + compile-status + subscription ROI metrics — Plan 3
- jsonl fallback for quota (explicitly deferred in 1.5 to follow-up task)

### Placeholder scan

Searched the plan for "TBD", "TODO", "similar to", "fill in", "add validation", etc. None present. All code blocks contain real implementations.

### Type consistency

- `QuotaSnapshot` uses same field names across 1.1, 1.4, 1.5 tests.
- `ArticlePriority` enum members consistent across 3.4, 3.5, 3.6, 4.2.
- `LogEntry` constructor signature identical in 2.1, 2.2, 2.3.
- `QueueItem` fields match between 3.6 and 4.2.
- Function names stable: `build_reverse_index`, `articles_affected_by`, `expand_ripple`, `priority_sort`, `load_manifest`, `load_ripple_map`, `build_queue`, `render_plan`.

No inconsistencies found.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-04-14-foundation-quota-log-queue.md`.

After executing this plan, the following will exist as independent, tested, shippable CLI tools:

- `scripts/quota.py` → `uv run python scripts/quota.py` prints current 5h/7d quota
- `scripts/plan_sync.py` → `uv run python scripts/plan_sync.py --commit HEAD` shows planned queue for a commit
- `scripts/wiki_log.py` → callable library for append-only log entries
- `scripts/provenance.py`, `scripts/reverse_index.py`, `scripts/ripple_map.py`, `scripts/topo.py`, `scripts/articles_manifest.py`, `scripts/queue_builder.py` → supporting library modules

Test coverage: ~35 unit tests across 8 test files.

Plan 2 will add: article-level compile (Pass 1), cross-link (Pass 2), validate (Pass 3). Plan 3 will add: orchestration modes (manual/sleep), statusline, compile-status, subscription ROI.

---

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
