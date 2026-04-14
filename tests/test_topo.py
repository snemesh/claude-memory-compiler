"""Tests for topo.py — priority-tier topological sort."""
import pytest

from topo import ArticlePriority, priority_sort


def test_priority_sort_orders_by_tier():
    # Intentionally mixed order
    articles = [
        ("api-gateway", ArticlePriority.SERVICES),
        ("fan-entity", ArticlePriority.MODELS),
        ("wallet", ArticlePriority.FEATURES),
        ("overview", ArticlePriority.OVERVIEW),
        ("turnkey", ArticlePriority.INTEGRATIONS),
    ]
    sorted_slugs = priority_sort(articles)
    assert sorted_slugs == [
        "fan-entity",        # MODELS
        "api-gateway",       # SERVICES
        "wallet",            # FEATURES
        "turnkey",           # INTEGRATIONS
        "overview",          # OVERVIEW (last)
    ]


def test_priority_sort_stable_within_tier():
    # Same tier → alphabetical fallback for deterministic output
    articles = [
        ("zebra", ArticlePriority.SERVICES),
        ("alpha", ArticlePriority.SERVICES),
        ("mike", ArticlePriority.SERVICES),
    ]
    assert priority_sort(articles) == ["alpha", "mike", "zebra"]


def test_priority_sort_empty():
    assert priority_sort([]) == []


def test_priority_tier_values_ordered():
    """Enum values must be ordered such that lower = earlier in pipeline."""
    assert ArticlePriority.MODELS.value < ArticlePriority.SERVICES.value
    assert ArticlePriority.SERVICES.value < ArticlePriority.FEATURES.value
    assert ArticlePriority.FEATURES.value < ArticlePriority.INTEGRATIONS.value
    assert ArticlePriority.INTEGRATIONS.value < ArticlePriority.OVERVIEW.value
