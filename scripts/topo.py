"""Priority-tier sort for the wiki compile queue.

Articles are processed foundations-first so that when article N is compiled,
every dependency it might link to is already present in the wiki. This is a
simple tier-based sort — true topological sort within a tier is not needed
at the current wiki size (~50 articles) and would require a hand-maintained
dependency graph we don't have.
"""
from __future__ import annotations

from enum import IntEnum


class ArticlePriority(IntEnum):
    MODELS = 1         # entities, value objects — pure vocabulary
    SERVICES = 2       # microservices, backends
    FEATURES = 3       # user-facing features that span services
    INTEGRATIONS = 4   # external deps: Turnkey, Atleta, PayX, Arena Verify
    OVERVIEW = 5       # synthesis layer — must see everything above


def priority_sort(articles: list[tuple[str, ArticlePriority]]) -> list[str]:
    """Sort (slug, priority) pairs by (priority, slug). Return slug list."""
    return [
        slug for slug, _ in sorted(articles, key=lambda item: (item[1].value, item[0]))
    ]
