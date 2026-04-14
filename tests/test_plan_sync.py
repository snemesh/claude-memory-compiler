"""Tests for plan_sync.py — dry-run queue planner CLI."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from articles_manifest import ArticleSpec
from provenance import Provenance, SourceRef, write_provenance
from plan_sync import render_plan
from queue_builder import QueueItem
from topo import ArticlePriority


def test_render_plan_empty():
    out = render_plan(queue=[], est_cost=0.0, avg_cost=0.10)
    assert "no articles" in out.lower()


def test_render_plan_shows_count_and_cost():
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
        QueueItem("fan-entity", ArticlePriority.MODELS, "manual"),
    ]
    out = render_plan(queue=queue, est_cost=0.30, avg_cost=0.10)
    assert "3 articles" in out
    assert "$0.30" in out
    assert "fan-entity" in out
    assert "clubs" in out


def test_render_plan_lists_trigger_origin():
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "ripple:api-gateway"),
    ]
    out = render_plan(queue=queue, est_cost=0.10, avg_cost=0.10)
    assert "ripple:api-gateway" in out
