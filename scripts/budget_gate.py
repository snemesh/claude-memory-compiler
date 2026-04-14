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
