# Orchestration Modes Implementation Plan (Manual + Sleep + Status)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the Plan 2 compile pipeline in two user-facing orchestration modes — manual (interactive foreground) and sleep (detached budget-aware daemon) — plus observability (`compile-status`, statusline one-liner, subscription ROI) and the E4 Pass-3-feedback-to-Pass-1 re-queue loop.

**Architecture:** All orchestration reads/writes a single `sync-state.json` file (atomic JSON). Budget decisions flow through a `budget_gate.py` that consumes `quota.get_quota()` + configurable thresholds. The sleep daemon is a plain Python loop — no launchd, no ticks — that checks budget, processes one chunk, persists state, sleeps across window boundaries when exhausted. Pass 3 re-queue loops one article through Pass 1 once with Haiku's issues inlined as feedback.

**Tech Stack:** Python 3.12+, existing Plan 1/2 modules (quota, jsonl_quota, pipeline, article_compiler, validator), `pytest`, `freezegun` for time-travel tests.

**Reference spec:** `docs/specs/2026-04-14-wiki-compile-chunked-design.md`
**Depends on Plan 1:** `scripts/{quota,jsonl_quota,wiki_log,queue_builder,articles_manifest,ripple_map,plan_sync}.py`
**Depends on Plan 2:** `scripts/{pipeline,article_compiler,validator,compile_queue}.py`

---

## File layout

```
scripts/
├── sync_state.py         # NEW: atomic sync-state.json reader/writer
├── budget_gate.py        # NEW: (snap, thresholds) → decision
├── sync.py               # NEW: manual foreground mode CLI
├── sync_loop.py          # NEW: sleep-mode daemon CLI
├── compile_status.py     # NEW: status command (human-readable dump)
├── statusline.py         # NEW: one-line statusline output (ccstatusline fmt)
└── roi.py                # NEW: monthly subscription ROI report
```

**Modified:**
- `scripts/pipeline.py` — add Pass 3 re-queue feedback option
- `scripts/article_compiler.py` — accept optional `feedback_issues` parameter

---

## Phase 1: Sync state persistence

### Task 1.1: SyncState dataclass + atomic write

**Files:**
- Create: `tests/test_sync_state.py`
- Create: `scripts/sync_state.py`

- [ ] **Step 1: Write failing tests for SyncState and load/save roundtrip**

Create `tests/test_sync_state.py`:

```python
"""Tests for sync_state.py — atomic sync-state.json persistence."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sync_state import QueueEntry, QueueStatus, SyncState, SyncStatus, load_state, save_state


def test_sync_state_roundtrip(tmp_path):
    path = tmp_path / "sync-state.json"
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk="clubs",
        sleep_until=None,
        queue=[
            QueueEntry(slug="fan-entity", priority_tier="MODELS",
                       status=QueueStatus.DONE, cost_usd=0.09,
                       commit_sha="abc123", started_at=None),
            QueueEntry(slug="clubs", priority_tier="SERVICES",
                       status=QueueStatus.RUNNING, cost_usd=None,
                       commit_sha=None, started_at=datetime(
                           2026, 4, 14, 22, 18, tzinfo=timezone.utc)),
        ],
        total_cost_usd=0.09,
        budget_used_5h_pct=31.4,
        budget_used_7d_pct=18.2,
    )
    save_state(path, state)
    loaded = load_state(path)

    assert loaded.status == SyncStatus.RUNNING
    assert loaded.current_chunk == "clubs"
    assert len(loaded.queue) == 2
    assert loaded.queue[0].status == QueueStatus.DONE
    assert loaded.queue[0].cost_usd == 0.09
    assert loaded.total_cost_usd == 0.09


def test_load_state_missing_file_returns_none(tmp_path):
    assert load_state(tmp_path / "absent.json") is None


def test_save_state_atomic_via_tmp_rename(tmp_path, monkeypatch):
    """save_state must write via a temp file + os.replace to avoid partial
    writes being observed by a concurrent reader.
    """
    path = tmp_path / "sync-state.json"
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[], total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    save_state(path, state)
    # Verify no leftover .tmp file
    assert not (tmp_path / "sync-state.json.tmp").exists()
    assert path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_state.py -v`
Expected: `ImportError: cannot import name 'QueueEntry'`

- [ ] **Step 3: Implement SyncState**

Create `scripts/sync_state.py`:

```python
"""Sync-state persistence: atomic JSON read/write for orchestration state.

Single source of truth for sync-loop status, used by:
  - sync_loop.py (writes current chunk + cost + sleep_until)
  - compile_status.py (reads to render human status)
  - sync.py (reads/writes for manual-mode progress)
  - statusline.py (reads to render one-line progress)
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class SyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SLEEPING = "sleeping"
    DONE = "done"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class QueueStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class QueueEntry:
    slug: str
    priority_tier: str  # "MODELS" | "SERVICES" | ...
    status: QueueStatus
    cost_usd: float | None
    commit_sha: str | None
    started_at: datetime | None


@dataclass(frozen=True)
class SyncState:
    status: SyncStatus
    started_at: datetime
    current_chunk: str | None
    sleep_until: datetime | None
    queue: list[QueueEntry]
    total_cost_usd: float
    budget_used_5h_pct: float
    budget_used_7d_pct: float


def _dt_to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _iso_to_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def save_state(path: Path, state: SyncState) -> None:
    """Atomic write: serialize to temp file, then rename."""
    data = {
        "status": state.status.value,
        "started_at": _dt_to_iso(state.started_at),
        "current_chunk": state.current_chunk,
        "sleep_until": _dt_to_iso(state.sleep_until),
        "queue": [
            {
                "slug": q.slug,
                "priority_tier": q.priority_tier,
                "status": q.status.value,
                "cost_usd": q.cost_usd,
                "commit_sha": q.commit_sha,
                "started_at": _dt_to_iso(q.started_at),
            }
            for q in state.queue
        ],
        "total_cost_usd": state.total_cost_usd,
        "budget_used_5h_pct": state.budget_used_5h_pct,
        "budget_used_7d_pct": state.budget_used_7d_pct,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_state(path: Path) -> SyncState | None:
    """Return SyncState or None if file absent / malformed."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return SyncState(
        status=SyncStatus(data["status"]),
        started_at=_iso_to_dt(data["started_at"]),
        current_chunk=data.get("current_chunk"),
        sleep_until=_iso_to_dt(data.get("sleep_until")),
        queue=[
            QueueEntry(
                slug=q["slug"],
                priority_tier=q["priority_tier"],
                status=QueueStatus(q["status"]),
                cost_usd=q.get("cost_usd"),
                commit_sha=q.get("commit_sha"),
                started_at=_iso_to_dt(q.get("started_at")),
            )
            for q in data.get("queue", [])
        ],
        total_cost_usd=float(data.get("total_cost_usd", 0.0)),
        budget_used_5h_pct=float(data.get("budget_used_5h_pct", 0.0)),
        budget_used_7d_pct=float(data.get("budget_used_7d_pct", 0.0)),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_state.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_state.py tests/test_sync_state.py
git commit -m "feat(sync): atomic sync-state.json persistence"
```

---

### Task 1.2: Kill-flag support in sync_state

**Files:**
- Modify: `tests/test_sync_state.py`
- Modify: `scripts/sync_state.py`

- [ ] **Step 1: Write failing tests for kill_flag**

Append to `tests/test_sync_state.py`:

```python
from sync_state import clear_kill_flag, read_kill_flag, set_kill_flag


def test_kill_flag_roundtrip(tmp_path):
    flag_path = tmp_path / "sync-state.kill"
    assert read_kill_flag(flag_path) is False
    set_kill_flag(flag_path)
    assert read_kill_flag(flag_path) is True
    clear_kill_flag(flag_path)
    assert read_kill_flag(flag_path) is False


def test_clear_kill_flag_is_idempotent(tmp_path):
    flag_path = tmp_path / "sync-state.kill"
    clear_kill_flag(flag_path)  # no-op when absent
    assert read_kill_flag(flag_path) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_state.py::test_kill_flag_roundtrip -v`
Expected: `ImportError: cannot import name 'set_kill_flag'`

- [ ] **Step 3: Implement kill flag helpers**

Append to `scripts/sync_state.py`:

```python
def set_kill_flag(path: Path) -> None:
    """Create a kill-flag marker file so the sync loop exits on next check."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop\n", encoding="utf-8")


def clear_kill_flag(path: Path) -> None:
    """Remove the kill-flag marker if present (no-op otherwise)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_kill_flag(path: Path) -> bool:
    """True if a kill flag exists at path."""
    return path.exists()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_state.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_state.py tests/test_sync_state.py
git commit -m "feat(sync): kill-flag marker for graceful daemon shutdown"
```

---

## Phase 2: Budget gate

### Task 2.1: Decision types + gate function

**Files:**
- Create: `tests/test_budget_gate.py`
- Create: `scripts/budget_gate.py`

- [ ] **Step 1: Write failing tests for budget gate**

Create `tests/test_budget_gate.py`:

```python
"""Tests for budget_gate.py — pre-chunk quota decisions."""
from datetime import datetime, timedelta, timezone

import pytest

from budget_gate import (
    Decision,
    DecisionKind,
    decide,
)
from quota import BudgetThresholds, QuotaSnapshot


def _snap(five_pct=50.0, seven_pct=30.0, resets_in_h=2.0):
    now = datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc)
    return QuotaSnapshot(
        five_hour_used_pct=five_pct,
        five_hour_resets_at=now + timedelta(hours=resets_in_h),
        seven_day_used_pct=seven_pct,
        seven_day_resets_at=now + timedelta(days=3),
        fetched_at=now,
        source="jsonl",
    )


def test_decide_continue_under_threshold():
    snap = _snap(five_pct=60.0, seven_pct=40.0)
    thresh = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    d = decide(snap, thresh)
    assert d.kind == DecisionKind.CONTINUE
    assert d.sleep_seconds == 0


def test_decide_sleep_on_five_hour_exhaust():
    snap = _snap(five_pct=90.0, resets_in_h=1.5)
    thresh = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    d = decide(snap, thresh)
    assert d.kind == DecisionKind.SLEEP
    # ~90 min + 30s safety
    assert 5400 < d.sleep_seconds < 5500
    assert "five_hour" in d.reason


def test_decide_abort_on_seven_day_exhaust():
    """7d cap is a hard wall — no point sleeping (would be days)."""
    snap = _snap(five_pct=40.0, seven_pct=95.0)
    thresh = BudgetThresholds(five_hour_stop_pct=85.0, seven_day_stop_pct=90.0)
    d = decide(snap, thresh)
    assert d.kind == DecisionKind.ABORT
    assert "seven_day" in d.reason


def test_decide_abort_on_missing_snapshot():
    """If quota is unreadable, don't burn quota — abort."""
    thresh = BudgetThresholds()
    d = decide(None, thresh)
    assert d.kind == DecisionKind.ABORT
    assert "unavailable" in d.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_budget_gate.py -v`
Expected: `ImportError: cannot import name 'Decision'`

- [ ] **Step 3: Implement budget_gate**

Create `scripts/budget_gate.py`:

```python
"""Pre-chunk budget decision.

Consumes a QuotaSnapshot + BudgetThresholds and emits a Decision:
  - CONTINUE: budget permits the next chunk, go ahead
  - SLEEP: 5h window exhausted, wait `sleep_seconds` until it resets
  - ABORT: 7d weekly cap hit (waiting would take days) OR quota unavailable

The sleep-loop uses this before every article compile.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from quota import BudgetThresholds, QuotaSnapshot, sleep_seconds_until_reset


class DecisionKind(str, Enum):
    CONTINUE = "continue"
    SLEEP = "sleep"
    ABORT = "abort"


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    sleep_seconds: int = 0
    reason: str = ""


def decide(snap: QuotaSnapshot | None, thresholds: BudgetThresholds) -> Decision:
    """Budget-gate decision."""
    if snap is None:
        return Decision(
            kind=DecisionKind.ABORT,
            reason="quota unavailable (no OAuth token and no jsonl fallback)",
        )

    if snap.seven_day_used_pct >= thresholds.seven_day_stop_pct:
        return Decision(
            kind=DecisionKind.ABORT,
            reason=f"seven_day quota at {snap.seven_day_used_pct:.1f}%"
                   f" (>= {thresholds.seven_day_stop_pct}%)",
        )

    if snap.five_hour_used_pct >= thresholds.five_hour_stop_pct:
        secs = sleep_seconds_until_reset(snap, safety_margin_s=30)
        return Decision(
            kind=DecisionKind.SLEEP,
            sleep_seconds=secs,
            reason=f"five_hour quota at {snap.five_hour_used_pct:.1f}%"
                   f" (>= {thresholds.five_hour_stop_pct}%)",
        )

    return Decision(kind=DecisionKind.CONTINUE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_budget_gate.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/budget_gate.py tests/test_budget_gate.py
git commit -m "feat(sync): budget_gate — pre-chunk quota decision"
```

---

## Phase 3: Manual sync (foreground)

### Task 3.1: sync.py manual mode with pre-flight estimate

**Files:**
- Create: `tests/test_sync_manual.py`
- Create: `scripts/sync.py`

- [ ] **Step 1: Write failing tests for preflight_summary**

Create `tests/test_sync_manual.py`:

```python
"""Tests for sync.py manual-mode preflight and execution."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from articles_manifest import ArticleSpec
from queue_builder import QueueItem
from quota import QuotaSnapshot
from sync import PreflightSummary, preflight_summary
from topo import ArticlePriority


def _snap(five_pct=50.0, seven_pct=30.0):
    now = datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc)
    return QuotaSnapshot(
        five_hour_used_pct=five_pct,
        five_hour_resets_at=now + timedelta(hours=2),
        seven_day_used_pct=seven_pct,
        seven_day_resets_at=now + timedelta(days=3),
        fetched_at=now,
        source="jsonl",
    )


def test_preflight_summary_renders_queue_and_cost():
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
    ]
    summary = preflight_summary(queue=queue, avg_cost=0.30, snap=_snap())
    assert isinstance(summary, PreflightSummary)
    assert summary.articles == 2
    assert summary.estimated_cost_usd == pytest.approx(0.60)
    assert "clubs" in summary.text
    assert "api-gateway" in summary.text
    assert "5h" in summary.text
    assert "72" in summary.text or "71" in summary.text  # 100 - 50 + rounding wiggle: remaining budget mention


def test_preflight_summary_handles_no_quota():
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]
    summary = preflight_summary(queue=queue, avg_cost=0.10, snap=None)
    assert summary.articles == 1
    assert "quota unavailable" in summary.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_manual.py -v`
Expected: `ImportError: cannot import name 'PreflightSummary'`

- [ ] **Step 3: Implement preflight_summary**

Create `scripts/sync.py`:

```python
"""Manual-mode sync: interactive foreground compile of a queue.

Flow:
  1. Build queue from --files or --commit (same as plan_sync).
  2. Compute preflight summary (queue + estimated cost + budget headroom).
  3. Print summary, ask user to confirm.
  4. Run pipeline synchronously, streaming progress to stdout.
  5. Emit log.md entries and sync-state.json along the way.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from articles_manifest import load_manifest
from pipeline import run_pipeline
from queue_builder import QueueItem, build_queue
from quota import QuotaSnapshot, get_quota
from ripple_map import load_ripple_map
from sync_state import (
    QueueEntry,
    QueueStatus,
    SyncState,
    SyncStatus,
    save_state,
)

_DEFAULT_AVG_COST_USD = 0.30  # empirical from smoke test 2026-04-14


@dataclass(frozen=True)
class PreflightSummary:
    articles: int
    estimated_cost_usd: float
    text: str


def preflight_summary(
    queue: list[QueueItem],
    avg_cost: float,
    snap: QuotaSnapshot | None,
) -> PreflightSummary:
    """Assemble the human-readable pre-run summary."""
    est = len(queue) * avg_cost
    lines: list[str] = [
        f"Queue: {len(queue)} articles, est cost ~${est:.2f} at ${avg_cost:.2f}/article",
    ]
    for i, item in enumerate(queue, 1):
        lines.append(f"  {i:2d}. [{item.priority.name}] {item.slug}  ({item.trigger})")

    if snap is not None:
        remaining_5h = 100.0 - snap.five_hour_used_pct
        remaining_7d = 100.0 - snap.seven_day_used_pct
        lines.append(
            f"Budget: 5h free {remaining_5h:.1f}%, 7d free {remaining_7d:.1f}% "
            f"(source: {snap.source})"
        )
    else:
        lines.append("Budget: quota unavailable")

    return PreflightSummary(
        articles=len(queue),
        estimated_cost_usd=est,
        text="\n".join(lines),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_manual.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync.py tests/test_sync_manual.py
git commit -m "feat(sync): preflight summary for manual mode"
```

---

### Task 3.2: sync.py run() orchestrator + CLI

**Files:**
- Modify: `tests/test_sync_manual.py`
- Modify: `scripts/sync.py`

- [ ] **Step 1: Write failing tests for run_manual_sync**

Append to `tests/test_sync_manual.py`:

```python
from articles_manifest import ArticleSpec
from llm_adapter import LLMResponse
from pipeline import PipelineReport
from sync import run_manual_sync


def test_run_manual_sync_invokes_pipeline_and_writes_state(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.001, model="claude-haiku-4-5")
        (cwd / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_manual_sync(
        queue=queue,
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
        log_file=wiki / "log.md",
        state_path=state_path,
    )

    assert isinstance(report, PipelineReport)
    assert report.articles_compiled == 1

    # Final state reflects completion
    from sync_state import SyncStatus, load_state
    state = load_state(state_path)
    assert state.status == SyncStatus.DONE
    assert state.total_cost_usd == report.total_cost_usd


def test_run_manual_sync_marks_failed_on_exception(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]
    queue = [QueueItem("x", ArticlePriority.SERVICES, "manual")]

    def fake_llm(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    with pytest.raises(RuntimeError):
        run_manual_sync(
            queue=queue, manifest=manifest, ripple_rules=[],
            repo_root=repo, wiki_dir=wiki,
            log_file=wiki / "log.md", state_path=state_path,
        )

    from sync_state import SyncStatus, load_state
    state = load_state(state_path)
    assert state.status == SyncStatus.FAILED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_manual.py::test_run_manual_sync_invokes_pipeline_and_writes_state -v`
Expected: `ImportError: cannot import name 'run_manual_sync'`

- [ ] **Step 3: Implement run_manual_sync + CLI**

Append to `scripts/sync.py`:

```python
from articles_manifest import ArticleSpec
from pipeline import PipelineReport
from ripple_map import RippleRule


def _initial_state(queue: list[QueueItem]) -> SyncState:
    return SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime.now(tz=timezone.utc),
        current_chunk=None,
        sleep_until=None,
        queue=[
            QueueEntry(
                slug=item.slug,
                priority_tier=item.priority.name,
                status=QueueStatus.PENDING,
                cost_usd=None,
                commit_sha=None,
                started_at=None,
            )
            for item in queue
        ],
        total_cost_usd=0.0,
        budget_used_5h_pct=0.0,
        budget_used_7d_pct=0.0,
    )


def run_manual_sync(
    queue: list[QueueItem],
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    repo_root: Path,
    wiki_dir: Path,
    log_file: Path | None,
    state_path: Path,
) -> PipelineReport:
    """Run the pipeline synchronously and track state on disk."""
    state = _initial_state(queue)
    save_state(state_path, state)

    try:
        report = run_pipeline(
            queue=queue,
            manifest=manifest,
            ripple_rules=ripple_rules,
            repo_root=repo_root,
            wiki_dir=wiki_dir,
            log_file=log_file,
        )
    except Exception:
        final = SyncState(
            status=SyncStatus.FAILED,
            started_at=state.started_at,
            current_chunk=state.current_chunk,
            sleep_until=None,
            queue=state.queue,
            total_cost_usd=state.total_cost_usd,
            budget_used_5h_pct=state.budget_used_5h_pct,
            budget_used_7d_pct=state.budget_used_7d_pct,
        )
        save_state(state_path, final)
        raise

    done_queue = [
        QueueEntry(
            slug=q.slug,
            priority_tier=q.priority_tier,
            status=QueueStatus.DONE,
            cost_usd=None,
            commit_sha=None,
            started_at=None,
        )
        for q in state.queue
    ]
    final = SyncState(
        status=SyncStatus.DONE,
        started_at=state.started_at,
        current_chunk=None,
        sleep_until=None,
        queue=done_queue,
        total_cost_usd=report.total_cost_usd,
        budget_used_5h_pct=0.0,
        budget_used_7d_pct=0.0,
    )
    save_state(state_path, final)
    return report


def _changed_files_from_commit(commit: str, repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--name-only",
         f"{commit}^", commit],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _main() -> int:
    parser = argparse.ArgumentParser(description="Manual-mode sync.")
    parser.add_argument("--files", nargs="*", default=None)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--wiki-dir", required=True)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--state-file", default=".memory/sync-state.json")
    parser.add_argument("--articles-manifest",
                        default="config/wiki-articles.yaml")
    parser.add_argument("--ripple-map", default="config/ripple-map.yaml")
    parser.add_argument("--yes", action="store_true",
                        help="skip interactive confirmation")
    args = parser.parse_args()

    if args.files is None and args.commit is None:
        parser.error("one of --files or --commit is required")

    repo = Path(args.repo_root).resolve()
    wiki = Path(args.wiki_dir).resolve()

    if args.commit:
        changed = _changed_files_from_commit(args.commit, repo)
        trigger = f"commit:{args.commit}"
    else:
        changed = list(args.files)
        trigger = "manual"

    manifest = load_manifest(Path(args.articles_manifest))
    ripple = load_ripple_map(Path(args.ripple_map))
    queue = build_queue(
        changed_files=changed, manifest=manifest,
        ripple_rules=ripple, wiki_dir=wiki, trigger=trigger,
    )
    if not queue:
        print("Nothing to compile.")
        return 0

    snap = get_quota()
    summary = preflight_summary(queue=queue, avg_cost=_DEFAULT_AVG_COST_USD, snap=snap)
    print(summary.text)

    if not args.yes:
        reply = input("\nProceed? [y/N] ").strip().lower()
        if reply != "y":
            print("aborted.")
            return 1

    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = repo / state_path

    report = run_manual_sync(
        queue=queue, manifest=manifest, ripple_rules=ripple,
        repo_root=repo, wiki_dir=wiki,
        log_file=Path(args.log_file) if args.log_file else None,
        state_path=state_path,
    )
    print(f"\nDone: {report.articles_compiled} articles, "
          f"${report.total_cost_usd:.2f} actual cost, "
          f"{report.links_added} links added")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_manual.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync.py tests/test_sync_manual.py
git commit -m "feat(sync): run_manual_sync + foreground CLI"
```

---

## Phase 4: Sleep-mode daemon

### Task 4.1: sync_loop.py core loop with budget-gate awareness

**Files:**
- Create: `tests/test_sync_loop.py`
- Create: `scripts/sync_loop.py`

- [ ] **Step 1: Write failing tests for process_one_chunk**

Create `tests/test_sync_loop.py`:

```python
"""Tests for sync_loop.py — detached budget-aware daemon."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from articles_manifest import ArticleSpec
from budget_gate import Decision, DecisionKind
from llm_adapter import LLMResponse
from queue_builder import QueueItem
from quota import BudgetThresholds, QuotaSnapshot
from sync_loop import LoopOutcome, LoopOutcomeKind, process_one_chunk
from sync_state import (
    QueueEntry,
    QueueStatus,
    SyncState,
    SyncStatus,
    save_state,
    load_state,
)
from topo import ArticlePriority


def _state_with_queue(slugs: list[str]) -> SyncState:
    return SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[
            QueueEntry(
                slug=s, priority_tier="SERVICES",
                status=QueueStatus.PENDING,
                cost_usd=None, commit_sha=None, started_at=None,
            ) for s in slugs
        ],
        total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )


def test_process_one_chunk_continue_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]
    thresh = BudgetThresholds()

    def fake_snap():
        return QuotaSnapshot(
            five_hour_used_pct=50.0,
            five_hour_resets_at=datetime(2026, 4, 14, 8, tzinfo=timezone.utc),
            seven_day_used_pct=20.0,
            seven_day_resets_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 4, 14, 3, tzinfo=timezone.utc),
            source="jsonl",
        )

    monkeypatch.setattr("sync_loop.get_quota", lambda: fake_snap())

    def fake_llm(*a, **kw):
        (wiki / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    outcome = process_one_chunk(
        state_path=state_path,
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
        log_file=None,
        thresholds=thresh,
    )
    assert outcome.kind == LoopOutcomeKind.CHUNK_DONE
    assert outcome.slug == "x"

    state = load_state(state_path)
    assert state.queue[0].status == QueueStatus.DONE


def test_process_one_chunk_sleep_path(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    def fake_snap():
        # 5h window exhausted → must sleep
        return QuotaSnapshot(
            five_hour_used_pct=95.0,
            five_hour_resets_at=datetime(2026, 4, 14, 5, tzinfo=timezone.utc),
            seven_day_used_pct=40.0,
            seven_day_resets_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 4, 14, 3, tzinfo=timezone.utc),
            source="jsonl",
        )

    monkeypatch.setattr("sync_loop.get_quota", lambda: fake_snap())

    outcome = process_one_chunk(
        state_path=state_path, manifest=manifest, ripple_rules=[],
        repo_root=tmp_path, wiki_dir=tmp_path, log_file=None,
        thresholds=BudgetThresholds(five_hour_stop_pct=85.0),
    )
    assert outcome.kind == LoopOutcomeKind.SLEEP
    assert outcome.sleep_seconds > 0


def test_process_one_chunk_honors_kill_flag(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    flag_path = tmp_path / "sync-state.kill"
    flag_path.write_text("stop\n")

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    outcome = process_one_chunk(
        state_path=state_path, manifest=manifest, ripple_rules=[],
        repo_root=tmp_path, wiki_dir=tmp_path, log_file=None,
        thresholds=BudgetThresholds(),
        kill_flag_path=flag_path,
    )
    assert outcome.kind == LoopOutcomeKind.KILLED


def test_process_one_chunk_queue_empty_returns_done(tmp_path):
    state_path = tmp_path / "state.json"
    save_state(state_path, _state_with_queue([]))

    outcome = process_one_chunk(
        state_path=state_path, manifest=[], ripple_rules=[],
        repo_root=tmp_path, wiki_dir=tmp_path, log_file=None,
        thresholds=BudgetThresholds(),
    )
    assert outcome.kind == LoopOutcomeKind.QUEUE_EMPTY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_loop.py -v`
Expected: `ImportError: cannot import name 'LoopOutcome'`

- [ ] **Step 3: Implement process_one_chunk**

Create `scripts/sync_loop.py`:

```python
"""Sleep-mode sync loop: detached budget-aware daemon.

Main loop runs one chunk per iteration:
  1. Refresh quota.get_quota()
  2. Budget gate: continue, sleep until window reset, or abort.
  3. Pick next PENDING chunk from sync-state.json.
  4. Compile via article_compiler.compile_article (Pass 1 + log).
  5. Persist updated state.
  6. Check kill-flag; exit if set.
  7. Loop until queue empty.

Pass 2 cross-link and Pass 3 validate are deferred to queue-drain
time (single sweep at end) so we don't re-read/re-lint every chunk.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from article_compiler import compile_article
from articles_manifest import ArticleSpec, load_manifest
from budget_gate import Decision, DecisionKind, decide
from crosslink import run_pass2
from glossary import build_glossary_block
from queue_builder import QueueItem
from quota import BudgetThresholds, get_quota
from ripple_map import RippleRule, load_ripple_map
from sync_state import (
    QueueEntry,
    QueueStatus,
    SyncState,
    SyncStatus,
    load_state,
    read_kill_flag,
    save_state,
)
from topo import ArticlePriority
from wiki_log import LogEntry, append_entry


class LoopOutcomeKind(str, Enum):
    CHUNK_DONE = "chunk_done"
    SLEEP = "sleep"
    QUEUE_EMPTY = "queue_empty"
    KILLED = "killed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class LoopOutcome:
    kind: LoopOutcomeKind
    slug: str | None = None
    sleep_seconds: int = 0
    reason: str = ""


def _next_pending(state: SyncState) -> QueueEntry | None:
    for entry in state.queue:
        if entry.status == QueueStatus.PENDING:
            return entry
    return None


def _update_entry(
    state: SyncState,
    slug: str,
    **kwargs,
) -> SyncState:
    new_queue = []
    for entry in state.queue:
        if entry.slug == slug:
            new_queue.append(QueueEntry(
                slug=entry.slug,
                priority_tier=entry.priority_tier,
                status=kwargs.get("status", entry.status),
                cost_usd=kwargs.get("cost_usd", entry.cost_usd),
                commit_sha=kwargs.get("commit_sha", entry.commit_sha),
                started_at=kwargs.get("started_at", entry.started_at),
            ))
        else:
            new_queue.append(entry)
    return SyncState(
        status=state.status,
        started_at=state.started_at,
        current_chunk=kwargs.get("_current_chunk", state.current_chunk),
        sleep_until=kwargs.get("_sleep_until", state.sleep_until),
        queue=new_queue,
        total_cost_usd=kwargs.get("_total_cost_usd", state.total_cost_usd),
        budget_used_5h_pct=kwargs.get("_budget_used_5h_pct", state.budget_used_5h_pct),
        budget_used_7d_pct=kwargs.get("_budget_used_7d_pct", state.budget_used_7d_pct),
    )


def process_one_chunk(
    state_path: Path,
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    repo_root: Path,
    wiki_dir: Path,
    log_file: Path | None,
    thresholds: BudgetThresholds,
    kill_flag_path: Path | None = None,
) -> LoopOutcome:
    """Single iteration of the sleep loop."""
    if kill_flag_path is not None and read_kill_flag(kill_flag_path):
        return LoopOutcome(kind=LoopOutcomeKind.KILLED, reason="kill flag set")

    state = load_state(state_path)
    if state is None:
        return LoopOutcome(kind=LoopOutcomeKind.ABORTED, reason="no state file")

    entry = _next_pending(state)
    if entry is None:
        return LoopOutcome(kind=LoopOutcomeKind.QUEUE_EMPTY)

    snap = get_quota()
    dec = decide(snap, thresholds)
    if dec.kind == DecisionKind.SLEEP:
        return LoopOutcome(
            kind=LoopOutcomeKind.SLEEP,
            sleep_seconds=dec.sleep_seconds,
            reason=dec.reason,
        )
    if dec.kind == DecisionKind.ABORT:
        return LoopOutcome(kind=LoopOutcomeKind.ABORTED, reason=dec.reason)

    spec = next((s for s in manifest if s.slug == entry.slug), None)
    if spec is None:
        # Skip orphans — mark failed, continue next iteration
        skipped_state = _update_entry(state, entry.slug, status=QueueStatus.FAILED)
        save_state(state_path, skipped_state)
        return LoopOutcome(
            kind=LoopOutcomeKind.CHUNK_DONE,
            slug=entry.slug,
            reason="no manifest spec",
        )

    # Mark running
    running_state = _update_entry(
        state, entry.slug,
        status=QueueStatus.RUNNING,
        started_at=datetime.now(tz=timezone.utc),
        _current_chunk=entry.slug,
    )
    save_state(state_path, running_state)

    item = QueueItem(
        slug=entry.slug,
        priority=ArticlePriority[entry.priority_tier],
        trigger="scheduled",
    )
    known_slugs = [s.slug for s in manifest]
    glossary = build_glossary_block(wiki_dir, known_slugs=known_slugs)
    result = compile_article(
        item=item, spec=spec, repo_root=repo_root,
        wiki_dir=wiki_dir, glossary_block=glossary, log_file=log_file,
    )

    # Mark done, update totals
    new_total = running_state.total_cost_usd + result.cost_usd
    done_state = _update_entry(
        running_state, entry.slug,
        status=QueueStatus.DONE,
        cost_usd=result.cost_usd,
        _current_chunk=None,
        _total_cost_usd=new_total,
        _budget_used_5h_pct=(snap.five_hour_used_pct if snap else 0.0),
        _budget_used_7d_pct=(snap.seven_day_used_pct if snap else 0.0),
    )
    save_state(state_path, done_state)
    return LoopOutcome(kind=LoopOutcomeKind.CHUNK_DONE, slug=entry.slug)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_loop.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_loop.py tests/test_sync_loop.py
git commit -m "feat(sync): sleep-loop process_one_chunk with budget gate"
```

---

### Task 4.2: Drive loop + Pass 2/3 sweep at end

**Files:**
- Modify: `tests/test_sync_loop.py`
- Modify: `scripts/sync_loop.py`

- [ ] **Step 1: Write failing tests for drive_loop**

Append to `tests/test_sync_loop.py`:

```python
from sync_loop import drive_loop


def test_drive_loop_processes_all_then_runs_pass2_and_exits(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    (repo / "x.go").write_text("")

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    def fake_snap():
        return QuotaSnapshot(
            five_hour_used_pct=10.0,
            five_hour_resets_at=datetime(2026, 4, 14, 8, tzinfo=timezone.utc),
            seven_day_used_pct=5.0,
            seven_day_resets_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 4, 14, 3, tzinfo=timezone.utc),
            source="jsonl",
        )

    monkeypatch.setattr("sync_loop.get_quota", lambda: fake_snap())

    def fake_llm(*a, **kw):
        (wiki / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    # Sleep is a no-op in tests
    monkeypatch.setattr("sync_loop.time.sleep", lambda s: None)

    drive_loop(
        state_path=state_path,
        manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        log_file=None, thresholds=BudgetThresholds(),
    )

    # Final state is DONE
    final = load_state(state_path)
    assert final.status == SyncStatus.DONE


def test_drive_loop_stops_on_kill_flag(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    state_path = tmp_path / "state.json"
    flag_path = tmp_path / "sync-state.kill"

    save_state(state_path, _state_with_queue(["x"]))
    manifest = [ArticleSpec("x", ArticlePriority.SERVICES, ["x.go"])]

    # Set kill flag BEFORE drive_loop starts
    flag_path.write_text("stop\n")
    monkeypatch.setattr("sync_loop.time.sleep", lambda s: None)

    drive_loop(
        state_path=state_path,
        manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        log_file=None, thresholds=BudgetThresholds(),
        kill_flag_path=flag_path,
    )

    final = load_state(state_path)
    assert final.status == SyncStatus.INTERRUPTED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_loop.py::test_drive_loop_processes_all_then_runs_pass2_and_exits -v`
Expected: `ImportError: cannot import name 'drive_loop'`

- [ ] **Step 3: Implement drive_loop**

Append to `scripts/sync_loop.py`:

```python
def _finalize(state_path: Path, status: SyncStatus) -> None:
    state = load_state(state_path)
    if state is None:
        return
    final = SyncState(
        status=status,
        started_at=state.started_at,
        current_chunk=None,
        sleep_until=None,
        queue=state.queue,
        total_cost_usd=state.total_cost_usd,
        budget_used_5h_pct=state.budget_used_5h_pct,
        budget_used_7d_pct=state.budget_used_7d_pct,
    )
    save_state(state_path, final)


def drive_loop(
    state_path: Path,
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    repo_root: Path,
    wiki_dir: Path,
    log_file: Path | None,
    thresholds: BudgetThresholds,
    kill_flag_path: Path | None = None,
) -> None:
    """Drive process_one_chunk until queue empty / killed / aborted.

    Sleeps in-process (time.sleep) when budget-gate says SLEEP. After queue
    drains, runs Pass 2 cross-link once across the whole wiki.
    """
    while True:
        outcome = process_one_chunk(
            state_path=state_path,
            manifest=manifest, ripple_rules=ripple_rules,
            repo_root=repo_root, wiki_dir=wiki_dir,
            log_file=log_file, thresholds=thresholds,
            kill_flag_path=kill_flag_path,
        )

        if outcome.kind == LoopOutcomeKind.CHUNK_DONE:
            continue
        if outcome.kind == LoopOutcomeKind.SLEEP:
            time.sleep(outcome.sleep_seconds)
            continue
        if outcome.kind == LoopOutcomeKind.QUEUE_EMPTY:
            # Pass 2 sweep at the end (deterministic, no LLM)
            known_slugs = [s.slug for s in manifest]
            report = run_pass2(wiki_dir=wiki_dir, known_slugs=known_slugs)
            if log_file is not None:
                append_entry(log_file, LogEntry(
                    ts=datetime.now(tz=timezone.utc),
                    event="lint",
                    summary=f"crosslink: {report.links_added} added",
                    metadata={"dangling": len(report.dangling)},
                ))
            _finalize(state_path, SyncStatus.DONE)
            return
        if outcome.kind == LoopOutcomeKind.KILLED:
            _finalize(state_path, SyncStatus.INTERRUPTED)
            return
        if outcome.kind == LoopOutcomeKind.ABORTED:
            _finalize(state_path, SyncStatus.FAILED)
            return


def _main() -> int:
    parser = argparse.ArgumentParser(description="Sleep-mode sync loop.")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--wiki-dir", required=True)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--articles-manifest",
                        default="config/wiki-articles.yaml")
    parser.add_argument("--ripple-map", default="config/ripple-map.yaml")
    parser.add_argument("--kill-flag", default=None)
    parser.add_argument("--five-hour-stop-pct", type=float, default=85.0)
    parser.add_argument("--seven-day-stop-pct", type=float, default=90.0)
    args = parser.parse_args()

    manifest = load_manifest(Path(args.articles_manifest))
    ripple = load_ripple_map(Path(args.ripple_map))
    thresholds = BudgetThresholds(
        five_hour_stop_pct=args.five_hour_stop_pct,
        seven_day_stop_pct=args.seven_day_stop_pct,
    )
    drive_loop(
        state_path=Path(args.state_file),
        manifest=manifest, ripple_rules=ripple,
        repo_root=Path(args.repo_root).resolve(),
        wiki_dir=Path(args.wiki_dir).resolve(),
        log_file=Path(args.log_file) if args.log_file else None,
        thresholds=thresholds,
        kill_flag_path=Path(args.kill_flag) if args.kill_flag else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_loop.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_loop.py tests/test_sync_loop.py
git commit -m "feat(sync): drive_loop with kill-flag, sleep, and Pass 2 finale"
```

---

### Task 4.3: Populate queue from changed files (sync-loop init helper)

**Files:**
- Modify: `tests/test_sync_loop.py`
- Modify: `scripts/sync_loop.py`

- [ ] **Step 1: Write failing test for prime_state_from_queue**

Append to `tests/test_sync_loop.py`:

```python
from sync_loop import prime_state_from_queue


def test_prime_state_from_queue_writes_initial_state(tmp_path):
    state_path = tmp_path / "state.json"
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
    ]
    prime_state_from_queue(state_path, queue)

    state = load_state(state_path)
    assert state.status == SyncStatus.PENDING
    assert len(state.queue) == 2
    assert [e.slug for e in state.queue] == ["clubs", "api-gateway"]
    assert all(e.status == QueueStatus.PENDING for e in state.queue)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sync_loop.py::test_prime_state_from_queue_writes_initial_state -v`
Expected: `ImportError: cannot import name 'prime_state_from_queue'`

- [ ] **Step 3: Implement prime_state_from_queue**

Append to `scripts/sync_loop.py`:

```python
def prime_state_from_queue(state_path: Path, queue: list[QueueItem]) -> None:
    """Write an initial SyncState with all items PENDING.

    Called by the orchestrator (e.g. when user says 'иду спать, синкай') to
    seed the daemon's work list. The daemon flips items to RUNNING then DONE
    as it processes them.
    """
    state = SyncState(
        status=SyncStatus.PENDING,
        started_at=datetime.now(tz=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[
            QueueEntry(
                slug=item.slug,
                priority_tier=item.priority.name,
                status=QueueStatus.PENDING,
                cost_usd=None, commit_sha=None, started_at=None,
            )
            for item in queue
        ],
        total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    save_state(state_path, state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_loop.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_loop.py tests/test_sync_loop.py
git commit -m "feat(sync): prime_state_from_queue initializer"
```

---

## Phase 5: Observability — status + statusline

### Task 5.1: compile_status.py — human-readable status dump

**Files:**
- Create: `tests/test_compile_status.py`
- Create: `scripts/compile_status.py`

- [ ] **Step 1: Write failing tests for render_status**

Create `tests/test_compile_status.py`:

```python
"""Tests for compile_status.py — human-readable sync status."""
from datetime import datetime, timezone

from compile_status import render_status
from sync_state import QueueEntry, QueueStatus, SyncState, SyncStatus


def test_render_status_summarises_running_sync():
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk="clubs",
        sleep_until=None,
        queue=[
            QueueEntry("fan-entity", "MODELS", QueueStatus.DONE, 0.09, None, None),
            QueueEntry("clubs", "SERVICES", QueueStatus.RUNNING, None, None, None),
            QueueEntry("api-gateway", "SERVICES", QueueStatus.PENDING, None, None, None),
        ],
        total_cost_usd=0.09,
        budget_used_5h_pct=45.0,
        budget_used_7d_pct=21.0,
    )
    out = render_status(state)
    assert "running" in out.lower()
    assert "1/3" in out or "1 of 3" in out
    assert "clubs" in out
    assert "$0.09" in out
    assert "5h:45" in out or "45%" in out


def test_render_status_done_state():
    state = SyncState(
        status=SyncStatus.DONE,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[
            QueueEntry("x", "SERVICES", QueueStatus.DONE, 0.10, None, None),
        ],
        total_cost_usd=0.10,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    out = render_status(state)
    assert "done" in out.lower()
    assert "1/1" in out or "1 of 1" in out


def test_render_status_sleeping_shows_resume_time():
    state = SyncState(
        status=SyncStatus.SLEEPING,
        started_at=datetime(2026, 4, 14, 22, 15, tzinfo=timezone.utc),
        current_chunk=None,
        sleep_until=datetime(2026, 4, 14, 23, 30, tzinfo=timezone.utc),
        queue=[
            QueueEntry("x", "SERVICES", QueueStatus.PENDING, None, None, None),
        ],
        total_cost_usd=0.05,
        budget_used_5h_pct=90.0, budget_used_7d_pct=30.0,
    )
    out = render_status(state)
    assert "sleeping" in out.lower()
    assert "23:30" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compile_status.py -v`
Expected: `ImportError: cannot import name 'render_status'`

- [ ] **Step 3: Implement compile_status**

Create `scripts/compile_status.py`:

```python
"""compile-status command: human-readable sync status dump.

Reads .memory/sync-state.json and renders a multi-line summary:
  - status, started at, elapsed
  - queue progress (done/total + phase breakdown)
  - current chunk (if running)
  - budget 5h/7d percentages
  - total cost + per-article average
  - sleep-until (if applicable)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sync_state import QueueStatus, SyncState, SyncStatus, load_state


def _counts(state: SyncState) -> dict[str, int]:
    counts = {"done": 0, "running": 0, "pending": 0, "failed": 0, "total": len(state.queue)}
    for entry in state.queue:
        if entry.status == QueueStatus.DONE:
            counts["done"] += 1
        elif entry.status == QueueStatus.RUNNING:
            counts["running"] += 1
        elif entry.status == QueueStatus.PENDING:
            counts["pending"] += 1
        elif entry.status == QueueStatus.FAILED:
            counts["failed"] += 1
    return counts


def render_status(state: SyncState) -> str:
    c = _counts(state)
    lines: list[str] = [
        f"status: {state.status.value}",
        f"started_at: {state.started_at.isoformat()}",
        f"progress: {c['done']}/{c['total']} done, {c['running']} running, "
        f"{c['pending']} pending"
        + (f", {c['failed']} failed" if c['failed'] else ""),
    ]
    if state.current_chunk:
        lines.append(f"current: {state.current_chunk}")
    if state.sleep_until:
        lines.append(f"sleeping until: {state.sleep_until.isoformat()}")
    lines.append(
        f"budget: 5h:{state.budget_used_5h_pct:.1f}% used, "
        f"7d:{state.budget_used_7d_pct:.1f}% used"
    )
    lines.append(f"total_cost_usd: ${state.total_cost_usd:.2f}")
    if c["done"] > 0:
        lines.append(f"avg_cost_per_article: "
                     f"${state.total_cost_usd / c['done']:.3f}")

    # Detailed queue listing
    lines.append("")
    lines.append("queue:")
    for entry in state.queue:
        cost_str = f" ${entry.cost_usd:.3f}" if entry.cost_usd is not None else ""
        lines.append(f"  [{entry.status.value:<7}] "
                     f"[{entry.priority_tier:<12}] "
                     f"{entry.slug}{cost_str}")
    return "\n".join(lines)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Show sync status.")
    parser.add_argument("--state-file", default=".memory/sync-state.json")
    args = parser.parse_args()

    path = Path(args.state_file)
    state = load_state(path)
    if state is None:
        print(f"no state file at {path}")
        return 1
    print(render_status(state))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compile_status.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/compile_status.py tests/test_compile_status.py
git commit -m "feat(sync): compile-status command"
```

---

### Task 5.2: statusline.py — one-liner for ccstatusline

**Files:**
- Create: `tests/test_statusline.py`
- Create: `scripts/statusline.py`

- [ ] **Step 1: Write failing tests for render_statusline**

Create `tests/test_statusline.py`:

```python
"""Tests for statusline.py — one-line progress format."""
from datetime import datetime, timezone

from statusline import render_statusline
from sync_state import QueueEntry, QueueStatus, SyncState, SyncStatus


def test_render_statusline_progress_bar_reflects_done_ratio():
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk="x", sleep_until=None,
        queue=[
            QueueEntry(f"a{i}", "SERVICES",
                       QueueStatus.DONE if i < 5 else QueueStatus.PENDING,
                       0.1 if i < 5 else None, None, None)
            for i in range(10)
        ],
        total_cost_usd=0.5,
        budget_used_5h_pct=40.0, budget_used_7d_pct=10.0,
    )
    out = render_statusline(state)
    # 5 of 10 done → ~50% filled
    assert "5/10" in out
    assert "50%" in out or "50.0%" in out
    assert "$0.5" in out or "$0.50" in out


def test_render_statusline_includes_budget():
    state = SyncState(
        status=SyncStatus.RUNNING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[QueueEntry("x", "SERVICES", QueueStatus.DONE, 0.1, None, None)],
        total_cost_usd=0.1,
        budget_used_5h_pct=68.2, budget_used_7d_pct=23.4,
    )
    out = render_statusline(state)
    assert "5h:68" in out
    assert "7d:23" in out


def test_render_statusline_sleeping_shown_explicitly():
    state = SyncState(
        status=SyncStatus.SLEEPING,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None,
        sleep_until=datetime(2026, 4, 14, 23, 30, tzinfo=timezone.utc),
        queue=[QueueEntry("x", "SERVICES", QueueStatus.PENDING, None, None, None)],
        total_cost_usd=0.0,
        budget_used_5h_pct=92.0, budget_used_7d_pct=40.0,
    )
    out = render_statusline(state)
    assert "sleep" in out.lower()


def test_render_statusline_handles_empty_queue():
    state = SyncState(
        status=SyncStatus.DONE,
        started_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        current_chunk=None, sleep_until=None,
        queue=[], total_cost_usd=0.0,
        budget_used_5h_pct=0.0, budget_used_7d_pct=0.0,
    )
    out = render_statusline(state)
    assert "0/0" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_statusline.py -v`
Expected: `ImportError: cannot import name 'render_statusline'`

- [ ] **Step 3: Implement statusline**

Create `scripts/statusline.py`:

```python
"""One-line statusline output (ccstatusline-compatible).

Format:
    🟦🟦🟦⬜⬜⬜ 5/10 (50%) • $0.50 • 5h:68% • 7d:23% [• sleeping-until HH:MM]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sync_state import QueueStatus, SyncState, SyncStatus, load_state


_BAR_CELLS = 10


def _progress_bar(done: int, total: int) -> str:
    if total == 0:
        return "⬜" * _BAR_CELLS
    filled = round(_BAR_CELLS * done / total)
    filled = max(0, min(_BAR_CELLS, filled))
    return "🟦" * filled + "⬜" * (_BAR_CELLS - filled)


def render_statusline(state: SyncState) -> str:
    done = sum(1 for e in state.queue if e.status == QueueStatus.DONE)
    total = len(state.queue)
    pct = (100.0 * done / total) if total else 0.0
    bar = _progress_bar(done, total)

    parts = [
        f"{bar} {done}/{total} ({pct:.1f}%)",
        f"${state.total_cost_usd:.2f}",
        f"5h:{state.budget_used_5h_pct:.1f}%",
        f"7d:{state.budget_used_7d_pct:.1f}%",
    ]
    if state.status == SyncStatus.SLEEPING and state.sleep_until:
        parts.append(f"sleeping until {state.sleep_until.strftime('%H:%M')}")
    return " • ".join(parts)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Render sync statusline.")
    parser.add_argument("--state-file", default=".memory/sync-state.json")
    args = parser.parse_args()

    state = load_state(Path(args.state_file))
    if state is None:
        print("")  # empty line when no sync in progress
        return 0
    print(render_statusline(state))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_statusline.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/statusline.py tests/test_statusline.py
git commit -m "feat(sync): one-line statusline output"
```

---

## Phase 6: Subscription ROI

### Task 6.1: Monthly ROI report

**Files:**
- Create: `tests/test_roi.py`
- Create: `scripts/roi.py`

- [ ] **Step 1: Write failing tests for monthly_roi**

Create `tests/test_roi.py`:

```python
"""Tests for roi.py — monthly subscription ROI report."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from roi import ROIReport, monthly_roi


def _write_jsonl(path: Path, ts: datetime, in_t: int, out_t: int,
                 model: str = "claude-sonnet-4-6") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {
            "model": model,
            "usage": {
                "input_tokens": in_t,
                "output_tokens": out_t,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    })
    path.write_text(line + "\n")


def test_monthly_roi_computes_ratio(tmp_path):
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    # Use sonnet 3$/1M input, 15$/1M output, 1M tokens in + 1M out = $18
    _write_jsonl(proj / "s.jsonl",
                 ts=now - timedelta(days=2),
                 in_t=1_000_000, out_t=1_000_000)

    report = monthly_roi(
        projects_dir=tmp_path / "projects",
        subscription_usd_monthly=200.0,
        now=now,
    )
    assert isinstance(report, ROIReport)
    assert report.subscription_usd == 200.0
    assert report.api_equivalent_usd == pytest.approx(18.0, rel=1e-3)
    # $18 / $200 * 100 = 9%
    assert report.roi_pct == pytest.approx(9.0, rel=1e-3)


def test_monthly_roi_excludes_prior_months(tmp_path):
    proj = tmp_path / "projects"
    proj.mkdir(parents=True)
    # An entry two months ago must not count
    _write_jsonl(proj / "s.jsonl",
                 ts=datetime(2026, 2, 10, tzinfo=timezone.utc),
                 in_t=1_000_000, out_t=1_000_000)
    report = monthly_roi(
        projects_dir=proj, subscription_usd_monthly=200.0,
        now=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )
    assert report.api_equivalent_usd == 0.0


def test_monthly_roi_missing_projects_dir_returns_zero(tmp_path):
    report = monthly_roi(
        projects_dir=tmp_path / "nope",
        subscription_usd_monthly=200.0,
        now=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )
    assert report.api_equivalent_usd == 0.0
    assert report.roi_pct == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roi.py -v`
Expected: `ImportError: cannot import name 'ROIReport'`

- [ ] **Step 3: Implement monthly_roi**

Create `scripts/roi.py`:

```python
"""Subscription ROI: month-to-date API-equivalent spend vs. subscription fee.

Reads jsonl logs for the current calendar month (1st of month 00:00 UTC
through `now`), sums cost via jsonl_quota pricing, computes ROI ratio
against the user's flat monthly subscription fee.

Use case: validate that the subscription is paying off. At peak usage a
$200/mo Max plan easily registers 10-100x that in API-equivalent spend.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jsonl_quota import estimate_window_cost_usd


@dataclass(frozen=True)
class ROIReport:
    subscription_usd: float
    api_equivalent_usd: float
    roi_pct: float  # api_equivalent / subscription * 100
    month_label: str  # e.g. "April 2026"


def _month_start_utc(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def monthly_roi(
    projects_dir: Path,
    subscription_usd_monthly: float,
    now: datetime | None = None,
) -> ROIReport:
    """Month-to-date API-equivalent spend compared to subscription fee."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    month_start = _month_start_utc(now)
    window = now - month_start

    api_equiv = estimate_window_cost_usd(projects_dir, window=window, now=now)
    pct = (100.0 * api_equiv / subscription_usd_monthly
           if subscription_usd_monthly > 0 else 0.0)
    return ROIReport(
        subscription_usd=subscription_usd_monthly,
        api_equivalent_usd=api_equiv,
        roi_pct=pct,
        month_label=now.strftime("%B %Y"),
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Monthly subscription ROI.")
    parser.add_argument("--subscription-usd", type=float, default=200.0)
    parser.add_argument("--projects-dir",
                        default=str(Path.home() / ".claude" / "projects"))
    args = parser.parse_args()

    report = monthly_roi(
        projects_dir=Path(args.projects_dir),
        subscription_usd_monthly=args.subscription_usd,
    )
    print(f"Month: {report.month_label}")
    print(f"Subscription paid:   ${report.subscription_usd:.2f}")
    print(f"API-equivalent used: ${report.api_equivalent_usd:.2f}")
    print(f"ROI:                 {report.roi_pct:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roi.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/roi.py tests/test_roi.py
git commit -m "feat(sync): monthly subscription ROI report"
```

---

## Phase 7: Pass 3 feedback loop (E4)

### Task 7.1: compile_article accepts feedback_issues

**Files:**
- Modify: `tests/test_article_compiler.py`
- Modify: `scripts/article_compiler.py`

- [ ] **Step 1: Write failing test for feedback_issues**

Append to `tests/test_article_compiler.py`:

```python
from validator import ValidationIssue


def test_compile_article_includes_feedback_in_prompt(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    (repo / "x.go").write_text("")

    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=["x.go"])
    item = QueueItem("x", ArticlePriority.SERVICES, "manual")

    captured_prompt = {}

    def fake_llm(prompt, model, cwd, max_turns=30):
        captured_prompt["p"] = prompt
        (wiki / "x.md").write_text("# X\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.10, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    compile_article(
        item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
        glossary_block="",
        feedback_issues=[
            ValidationIssue(
                kind="stale",
                description="Article says GET-only but code accepts all methods",
                evidence="table shows GET; handler has no method check",
            ),
        ],
    )
    # Feedback must be visible in the prompt passed to LLM
    assert "stale" in captured_prompt["p"]
    assert "GET-only" in captured_prompt["p"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_article_compiler.py::test_compile_article_includes_feedback_in_prompt -v`
Expected: `TypeError: compile_article() got an unexpected keyword argument 'feedback_issues'`

- [ ] **Step 3: Add feedback_issues parameter**

In `scripts/article_compiler.py`, modify `build_pass1_prompt` and `compile_article`:

First, extend `build_pass1_prompt` to accept feedback:

```python
def build_pass1_prompt(
    spec: ArticleSpec,
    source_refs: list[SourceRef],
    source_contents: dict[str, str],
    glossary_block: str,
    existing_body: str | None,
    trigger: str,
    feedback_issues: list | None = None,
) -> str:
    """Construct the Pass 1 prompt for the LLM.

    `source_contents` maps path → file body. Each file is truncated to
    _PER_FILE_MAX_CHARS to keep the prompt bounded.
    `feedback_issues` is an optional list of ValidationIssue objects
    from a prior Pass 3 run that must be addressed in this compile.
    """
    mode = "update" if existing_body else "create"
    lines: list[str] = [
        f"# Compile wiki article: {spec.slug}",
        "",
        f"**Priority tier:** {spec.priority.name}",
        f"**Mode:** {mode} (trigger: {trigger})",
        "",
    ]

    if glossary_block:
        lines.append(glossary_block)
        lines.append("")

    if feedback_issues:
        lines.append("## Feedback from previous validation pass (must be addressed)")
        lines.append("")
        for issue in feedback_issues:
            lines.append(f"- **{issue.kind}**: {issue.description}")
            if issue.evidence:
                lines.append(f"  - evidence: {issue.evidence}")
        lines.append("")

    if existing_body is not None:
        lines.append("## Existing article body (to refine)")
        lines.append("")
        lines.append(_truncate(existing_body, _PER_FILE_MAX_CHARS))
        lines.append("")

    lines.append("## Source files")
    lines.append("")
    for ref in source_refs:
        content = source_contents.get(ref.path, "")
        lines.append(f"### `{ref.path}` (sha:{ref.sha})")
        lines.append("")
        lines.append("```")
        lines.append(_truncate(content, _PER_FILE_MAX_CHARS))
        lines.append("```")
        lines.append("")

    lines.append("## Your task")
    lines.append("")
    lines.append(
        f"{'Create' if mode == 'create' else 'Update'} the article at the "
        f"wiki location for slug `{spec.slug}`. Link to other wiki pages via "
        f"`[[slug]]` where relevant (see pinned slug list above). Use "
        f"encyclopedia tone: neutral, third-person, complete. Focus on the "
        f"information present in the source files; do not invent."
    )
    if feedback_issues:
        lines.append(
            "\nExplicitly address the feedback issues listed above — each "
            "one reflects a contradiction or staleness that prior output "
            "contained."
        )

    return "\n".join(lines)
```

Then add `feedback_issues` to `compile_article`:

```python
def compile_article(
    item: QueueItem,
    spec: ArticleSpec,
    repo_root: Path,
    wiki_dir: Path,
    glossary_block: str,
    log_file: Path | None = None,
    feedback_issues: list | None = None,
) -> CompileResult:
    """Run Pass 1 on a single article, optionally with Pass-3 feedback."""
    paths = resolve_sources(spec, repo_root)
    refs = build_source_refs(paths, repo_root)
    contents = {ref.path: (repo_root / ref.path).read_text(encoding="utf-8",
                                                            errors="ignore")
                for ref in refs}

    article_path = wiki_dir / f"{spec.slug}.md"
    existing_body = _read_article_body(article_path)

    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=refs,
        source_contents=contents,
        glossary_block=glossary_block,
        existing_body=existing_body,
        trigger=item.trigger,
        feedback_issues=feedback_issues,
    )

    response = call_llm(
        prompt=prompt,
        model=_MODEL_PASS1,
        cwd=wiki_dir,
        max_turns=_DEFAULT_MAX_TURNS,
    )

    if not article_path.exists():
        article_path.write_text(f"# {spec.slug}\n\n(LLM did not emit a body.)\n",
                                encoding="utf-8")

    now = datetime.now(tz=timezone.utc)
    prov = Provenance(
        sources=refs,
        compiled_at=now,
        model=response.model,
        cost_usd=response.cost_usd,
        triggered_by=item.trigger if not feedback_issues else f"{item.trigger}+feedback",
    )
    write_provenance(article_path, prov)

    if log_file is not None:
        append_entry(log_file, LogEntry(
            ts=now,
            event="ingest",
            summary=spec.slug,
            metadata={
                "cost_usd": round(response.cost_usd, 4),
                "sources": len(refs),
                "model": response.model,
                "trigger": item.trigger,
                "feedback": len(feedback_issues) if feedback_issues else 0,
            },
        ))

    return CompileResult(
        slug=spec.slug,
        cost_usd=response.cost_usd,
        sources_count=len(refs),
        model=response.model,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_article_compiler.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/article_compiler.py tests/test_article_compiler.py
git commit -m "feat(compile): feedback_issues parameter for Pass 3 → Pass 1 re-queue"
```

---

### Task 7.2: run_pipeline re-queue on Pass 3 issues

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `scripts/pipeline.py`

- [ ] **Step 1: Write failing test for re-queue behavior**

Append to `tests/test_pipeline.py`:

```python
def test_run_pipeline_requeues_articles_with_pass3_issues(
    repo_and_wiki, manifest, monkeypatch
):
    """When Pass 3 finds issues, Pass 1 is invoked again with feedback."""
    repo, wiki = repo_and_wiki
    compile_calls: list[str] = []

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            # First validation: returns a stale issue.
            # Subsequent validations (after re-compile): clean.
            nonlocal validate_call_count
            validate_call_count += 1
            if validate_call_count == 1:
                return LLMResponse(
                    text='```json\n[{"kind": "stale", '
                         '"description": "docs claim GET-only", '
                         '"evidence": "table + handler"}]\n```',
                    cost_usd=0.002, model="claude-haiku-4-5",
                )
            return LLMResponse(text="```json\n[]\n```", cost_usd=0.002,
                               model="claude-haiku-4-5")
        # Pass 1: record prompt, write file, count invocations
        slug = prompt.split("\n", 1)[0].rsplit(":", 1)[-1].strip()
        compile_calls.append(prompt)
        (cwd / f"{slug}.md").write_text(f"# {slug}\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.05, model="claude-sonnet-4-6")

    validate_call_count = 0
    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_pipeline(
        queue=[QueueItem("clubs", ArticlePriority.SERVICES, "manual")],
        manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
        enable_feedback_loop=True,
    )
    # 2 Pass 1 calls: initial + re-compile with feedback
    assert len(compile_calls) == 2
    # The second prompt must contain the feedback text
    assert "docs claim GET-only" in compile_calls[1]
    # Cost accumulates both compiles + both validations
    assert report.total_cost_usd == pytest.approx(0.05 * 2 + 0.002 * 2, rel=1e-3)


def test_run_pipeline_feedback_loop_disabled_by_default(
    repo_and_wiki, manifest, monkeypatch
):
    """Without enable_feedback_loop, Pass 3 issues are logged but no re-compile."""
    repo, wiki = repo_and_wiki
    compile_calls: list[str] = []

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(
                text='```json\n[{"kind": "stale", '
                     '"description": "X", "evidence": "Y"}]\n```',
                cost_usd=0.002, model="claude-haiku-4-5",
            )
        slug = prompt.split("\n", 1)[0].rsplit(":", 1)[-1].strip()
        compile_calls.append(prompt)
        (cwd / f"{slug}.md").write_text(f"# {slug}\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.05, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_pipeline(
        queue=[QueueItem("clubs", ArticlePriority.SERVICES, "manual")],
        manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
    )
    # Only one Pass 1 call despite finding an issue
    assert len(compile_calls) == 1
    assert len(report.validation_results[0].issues) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py::test_run_pipeline_requeues_articles_with_pass3_issues -v`
Expected: `TypeError: run_pipeline() got an unexpected keyword argument 'enable_feedback_loop'`

- [ ] **Step 3: Add re-queue loop to run_pipeline**

Modify `scripts/pipeline.py` `run_pipeline` signature and add the feedback loop section. Replace the function with:

```python
def run_pipeline(
    queue: list[QueueItem],
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    repo_root: Path,
    wiki_dir: Path,
    log_file: Path | None = None,
    enable_feedback_loop: bool = False,
) -> PipelineReport:
    """Run the full three-pass pipeline over a compile queue.

    When enable_feedback_loop=True, any article whose Pass 3 validation
    finds issues is re-compiled once with the issues inlined as feedback.
    """
    if not queue:
        return PipelineReport(
            articles_compiled=0,
            total_cost_usd=0.0,
            links_added=0,
            dangling_refs={},
            validation_results=[],
        )

    known_slugs = [s.slug for s in manifest]
    glossary = build_glossary_block(wiki_dir, known_slugs=known_slugs)
    spec_by_slug = {s.slug: s for s in manifest}

    total_cost = 0.0
    compiled = 0

    # ── Pass 1 ────────────────────────────────────────────────────
    for item in queue:
        spec = spec_by_slug.get(item.slug)
        if spec is None:
            continue
        result = compile_article(
            item=item, spec=spec,
            repo_root=repo_root, wiki_dir=wiki_dir,
            glossary_block=glossary, log_file=log_file,
        )
        total_cost += result.cost_usd
        compiled += 1

    # ── Pass 2 ────────────────────────────────────────────────────
    pass2 = run_pass2(wiki_dir=wiki_dir, known_slugs=known_slugs)
    if log_file is not None:
        append_entry(log_file, LogEntry(
            ts=datetime.now(tz=timezone.utc),
            event="lint",
            summary=f"crosslink: {pass2.links_added} added",
            metadata={"dangling": len(pass2.dangling)},
        ))

    # ── Pass 3 ────────────────────────────────────────────────────
    validations: list[ValidationResult] = []
    for item in queue:
        siblings = _siblings_for(item.slug, manifest, ripple_rules)
        result = validate_article(
            article_slug=item.slug,
            wiki_dir=wiki_dir,
            sibling_slugs=siblings,
        )
        validations.append(result)
        total_cost += result.cost_usd
        if log_file is not None and result.issues:
            append_entry(log_file, LogEntry(
                ts=datetime.now(tz=timezone.utc),
                event="lint",
                summary=f"validate {item.slug}: {len(result.issues)} issues",
                metadata={"kinds": ",".join(i.kind for i in result.issues)},
            ))

    # ── Feedback re-queue (E4) ────────────────────────────────────
    if enable_feedback_loop:
        for validation in validations:
            if not validation.issues:
                continue
            item = next((q for q in queue if q.slug == validation.slug), None)
            if item is None:
                continue
            spec = spec_by_slug.get(item.slug)
            if spec is None:
                continue

            recompile = compile_article(
                item=item, spec=spec,
                repo_root=repo_root, wiki_dir=wiki_dir,
                glossary_block=glossary, log_file=log_file,
                feedback_issues=validation.issues,
            )
            total_cost += recompile.cost_usd

            # Re-validate after feedback compile
            re_val = validate_article(
                article_slug=item.slug,
                wiki_dir=wiki_dir,
                sibling_slugs=_siblings_for(item.slug, manifest, ripple_rules),
            )
            total_cost += re_val.cost_usd
            # Replace the earlier validation result with the post-feedback one
            for i, v in enumerate(validations):
                if v.slug == re_val.slug:
                    validations[i] = re_val
                    break

    return PipelineReport(
        articles_compiled=compiled,
        total_cost_usd=total_cost,
        links_added=pass2.links_added,
        dangling_refs=pass2.dangling,
        validation_results=validations,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: `6 passed` (4 existing + 2 new).

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/pipeline.py tests/test_pipeline.py
git commit -m "feat(compile): E4 Pass 3 → Pass 1 feedback re-queue loop"
```

---

## Self-Review

### Spec coverage

| Spec section | Task(s) |
|---|---|
| Manual mode (foreground, preflight, confirmation) | 3.1, 3.2 |
| Sleep mode (detached daemon, budget-aware loop) | 4.1, 4.2, 4.3 |
| `sync-state.json` atomic persistence | 1.1 |
| Kill-flag graceful shutdown | 1.2 |
| Budget gate (continue/sleep/abort) | 2.1 |
| `compile-status` command | 5.1 |
| Statusline one-liner | 5.2 |
| Subscription ROI metrics | 6.1 |
| E4 Pass 3 → Pass 1 feedback loop | 7.1, 7.2 |

Spec items fully covered by completed Plans 1 + 2 and not re-visited here:
- Provenance frontmatter (Plan 1 + Plan 2 Task 4.2)
- log.md append (Plan 1 + Plan 2 Task 4.3)
- oauth/usage + jsonl fallback (Plan 1)
- Pass 1/2/3 compile pipeline (Plan 2)
- E1 reverse index, E2 ripple map, E3 pinned glossary, E5 commit-atomic batching (Plan 1 + Plan 2)

Intentionally NOT included (user preference / YAGNI):
- Multi-line statusline with color-coded progress cells — single-line is sufficient at current scale; users can compose richer views on top.
- launchd/systemd integration — design decision finalized during brainstorm.

### Placeholder scan

Scanned for "TBD", "TODO", "similar to", "fill in", "add validation", "handle edge cases". None present. Every task has complete code.

### Type consistency

- `SyncState` + `QueueEntry` + `SyncStatus` + `QueueStatus` stable across 1.1, 1.2, 3.2, 4.1, 4.2, 4.3, 5.1, 5.2.
- `Decision` + `DecisionKind` stable between 2.1 and 4.1.
- `LoopOutcome` + `LoopOutcomeKind` stable between 4.1 and 4.2.
- `ROIReport` stable between 6.1 and any future consumer.
- `run_pipeline` signature change in 7.2 (added `enable_feedback_loop`) is backward-compatible (default False preserves Plan 2 behavior) — verified by re-test in 7.2 Step 5.
- `compile_article` signature change in 7.1 (added `feedback_issues`) is backward-compatible (default None preserves Plan 2 behavior).
- `QuotaSnapshot`, `BudgetThresholds`, `QueueItem`, `ArticleSpec`, `ArticlePriority`, `ValidationIssue`, `ValidationResult`, `PipelineReport` — all reused unchanged from Plans 1 + 2.

No inconsistencies found.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-04-14-orchestration-modes.md`.

After executing this plan:
- **`uv run python scripts/sync.py --commit HEAD --repo-root . --wiki-dir vault/arena/wiki --log-file vault/arena/log.md --state-file .memory/sync-state.json`** runs manual mode with preflight + confirmation.
- **`nohup uv run python scripts/sync_loop.py --state-file .memory/sync-state.json --repo-root . --wiki-dir vault/arena/wiki --log-file vault/arena/log.md --kill-flag .memory/sync-state.kill &`** starts sleep-mode daemon.
- **`uv run python scripts/compile_status.py`** shows detailed sync status.
- **`uv run python scripts/statusline.py`** emits one-line progress (for ccstatusline / terminal prompt).
- **`uv run python scripts/roi.py`** prints monthly subscription ROI.
- Pipeline gains optional `enable_feedback_loop=True` for Pass 3 → Pass 1 re-queue.

Test coverage added: ~24 new unit tests (on top of Plan 1 + Plan 2's 123 → ~147 total).

This is the final plan derived from the spec — after execution, the system described in `docs/specs/2026-04-14-wiki-compile-chunked-design.md` is complete and operational end-to-end.

---

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
