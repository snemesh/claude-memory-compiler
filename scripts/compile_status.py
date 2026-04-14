"""compile-status command: human-readable sync status dump."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sync_state import QueueStatus, SyncState, load_state


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
