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
