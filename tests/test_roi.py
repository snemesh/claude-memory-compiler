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
    assert report.roi_pct == pytest.approx(9.0, rel=1e-3)


def test_monthly_roi_excludes_prior_months(tmp_path):
    proj = tmp_path / "projects"
    proj.mkdir(parents=True)
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
