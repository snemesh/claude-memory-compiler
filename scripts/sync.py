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

from article_compiler import CompileResult
from articles_manifest import ArticleSpec, load_manifest
from pipeline import PipelineReport, run_pipeline
from queue_builder import QueueItem, build_queue
from quota import QuotaSnapshot, get_quota
from ripple_map import RippleRule, load_ripple_map
from sync_state import (
    QueueEntry,
    QueueStatus,
    SyncState,
    SyncStatus,
    load_state,
    save_state,
)

_DEFAULT_AVG_COST_USD = 0.30


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


def _on_chunk_done(state_path: Path, slug: str, result: CompileResult) -> None:
    """Per-chunk callback: flip entry to DONE, record cost, persist atomically.

    Called after every Pass 1 compile. This is what makes `compile-status`
    show live progress during a running manual sync. Also refreshes
    budget_used_*_pct fields from quota.get_quota() so statusline reflects
    the latest burn rate without requiring a separate poll.
    """
    state = load_state(state_path)
    if state is None:
        return
    new_queue = []
    for entry in state.queue:
        if entry.slug == slug:
            new_queue.append(QueueEntry(
                slug=entry.slug,
                priority_tier=entry.priority_tier,
                status=QueueStatus.DONE,
                cost_usd=result.cost_usd,
                commit_sha=entry.commit_sha,
                started_at=entry.started_at,
            ))
        else:
            new_queue.append(entry)

    snap = get_quota()
    budget_5h = snap.five_hour_used_pct if snap else state.budget_used_5h_pct
    budget_7d = snap.seven_day_used_pct if snap else state.budget_used_7d_pct

    updated = SyncState(
        status=state.status,
        started_at=state.started_at,
        current_chunk=None,
        sleep_until=state.sleep_until,
        queue=new_queue,
        total_cost_usd=state.total_cost_usd + result.cost_usd,
        budget_used_5h_pct=budget_5h,
        budget_used_7d_pct=budget_7d,
    )
    save_state(state_path, updated)


def run_manual_sync(
    queue: list[QueueItem],
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    repo_root: Path,
    wiki_dir: Path,
    log_file: Path | None,
    state_path: Path,
) -> PipelineReport:
    """Run the pipeline synchronously and track state on disk.

    State file is updated live per Pass 1 chunk so `compile-status` and
    `statusline` see progress in real time during a running sync.
    """
    state = _initial_state(queue)
    save_state(state_path, state)

    def progress(slug: str, result: CompileResult) -> None:
        _on_chunk_done(state_path, slug, result)

    try:
        report = run_pipeline(
            queue=queue,
            manifest=manifest,
            ripple_rules=ripple_rules,
            repo_root=repo_root,
            wiki_dir=wiki_dir,
            log_file=log_file,
            on_pass1_chunk=progress,
        )
    except Exception:
        state_now = load_state(state_path) or state
        final = SyncState(
            status=SyncStatus.FAILED,
            started_at=state_now.started_at,
            current_chunk=None,
            sleep_until=None,
            queue=state_now.queue,
            total_cost_usd=state_now.total_cost_usd,
            budget_used_5h_pct=state_now.budget_used_5h_pct,
            budget_used_7d_pct=state_now.budget_used_7d_pct,
        )
        save_state(state_path, final)
        raise

    # Final: DONE status, reconcile total from report, keep per-entry cost.
    state_now = load_state(state_path) or state
    final = SyncState(
        status=SyncStatus.DONE,
        started_at=state_now.started_at,
        current_chunk=None,
        sleep_until=None,
        queue=state_now.queue,
        total_cost_usd=report.total_cost_usd,
        budget_used_5h_pct=state_now.budget_used_5h_pct,
        budget_used_7d_pct=state_now.budget_used_7d_pct,
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
