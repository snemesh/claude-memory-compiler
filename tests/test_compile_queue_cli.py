"""Tests for compile_queue.py — thin CLI around run_pipeline."""
import pytest

from compile_queue import render_report
from pipeline import PipelineReport
from validator import ValidationIssue, ValidationResult


def test_render_report_summarises_counts():
    report = PipelineReport(
        articles_compiled=3,
        total_cost_usd=0.37,
        links_added=7,
        dangling_refs={"clubs": ["ghost"]},
        validation_results=[
            ValidationResult(slug="clubs", issues=[], cost_usd=0.001,
                             model="claude-haiku-4-5"),
        ],
    )
    out = render_report(report)
    assert "3 articles" in out
    assert "$0.37" in out
    assert "7 links added" in out
    assert "ghost" in out


def test_render_report_lists_validation_issues():
    report = PipelineReport(
        articles_compiled=1,
        total_cost_usd=0.05,
        links_added=0,
        dangling_refs={},
        validation_results=[
            ValidationResult(
                slug="clubs",
                issues=[ValidationIssue(kind="stale", description="X", evidence="q")],
                cost_usd=0.001,
                model="claude-haiku-4-5",
            ),
        ],
    )
    out = render_report(report)
    assert "stale" in out
    assert "clubs" in out
