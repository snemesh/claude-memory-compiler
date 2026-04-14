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
