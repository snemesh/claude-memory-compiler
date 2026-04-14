"""Sleep-mode sync loop: detached budget-aware daemon.

Main loop runs one chunk per iteration:
  1. Refresh quota.get_quota()
  2. Budget gate: continue, sleep until window reset, or abort.
  3. Pick next PENDING chunk from sync-state.json.
  4. Compile via article_compiler.compile_article (Pass 1 + log).
  5. Persist updated state.
  6. Check kill-flag; exit if set.
  7. Loop until queue empty.

Pass 2 cross-link runs once at queue-drain time (single sweep at end).
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
from budget_gate import DecisionKind, decide
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
    *,
    status: QueueStatus | None = None,
    cost_usd: float | None = None,
    commit_sha: str | None = None,
    started_at: datetime | None = None,
    _current_chunk: str | None | type = type,
    _sleep_until: datetime | None | type = type,
    _total_cost_usd: float | None = None,
    _budget_used_5h_pct: float | None = None,
    _budget_used_7d_pct: float | None = None,
) -> SyncState:
    """Return a copy of `state` with the named entry updated + optional state-level fields.

    The sentinel `type` distinguishes "not provided" from "set to None".
    """
    new_queue = []
    for entry in state.queue:
        if entry.slug == slug:
            new_queue.append(QueueEntry(
                slug=entry.slug,
                priority_tier=entry.priority_tier,
                status=status if status is not None else entry.status,
                cost_usd=cost_usd if cost_usd is not None else entry.cost_usd,
                commit_sha=commit_sha if commit_sha is not None else entry.commit_sha,
                started_at=started_at if started_at is not None else entry.started_at,
            ))
        else:
            new_queue.append(entry)
    return SyncState(
        status=state.status,
        started_at=state.started_at,
        current_chunk=(state.current_chunk if _current_chunk is type else _current_chunk),
        sleep_until=(state.sleep_until if _sleep_until is type else _sleep_until),
        queue=new_queue,
        total_cost_usd=(_total_cost_usd if _total_cost_usd is not None
                        else state.total_cost_usd),
        budget_used_5h_pct=(_budget_used_5h_pct if _budget_used_5h_pct is not None
                            else state.budget_used_5h_pct),
        budget_used_7d_pct=(_budget_used_7d_pct if _budget_used_7d_pct is not None
                            else state.budget_used_7d_pct),
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
        skipped_state = _update_entry(state, entry.slug, status=QueueStatus.FAILED)
        save_state(state_path, skipped_state)
        return LoopOutcome(
            kind=LoopOutcomeKind.CHUNK_DONE,
            slug=entry.slug,
            reason="no manifest spec",
        )

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
