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
        print("")
        return 0
    print(render_statusline(state))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
