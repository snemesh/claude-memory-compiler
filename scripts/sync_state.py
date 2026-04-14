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
from dataclasses import dataclass
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
    priority_tier: str
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
