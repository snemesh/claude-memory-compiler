"""Queue builder: changed files → ordered list of QueueItem for compile pipeline.

Sources of article candidates:
1. Reverse provenance index — articles that literally list a changed file as a source
2. Ripple map — articles declared via fnmatch patterns in ripple-map.yaml

Union both, deduplicate, sort by priority tier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from articles_manifest import ArticleSpec
from reverse_index import articles_affected_by, build_reverse_index
from ripple_map import RippleRule, expand_ripple
from topo import ArticlePriority, priority_sort


@dataclass(frozen=True)
class QueueItem:
    slug: str
    priority: ArticlePriority
    trigger: str  # "manual" | "ripple:<slug>" | "commit:<sha>"


def build_queue(
    changed_files: list[str],
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    wiki_dir: Path,
    trigger: str,
) -> list[QueueItem]:
    """Compute ordered queue of articles to (re)compile.

    Returns [] if no article is affected.
    """
    # Union sources of staleness
    reverse_idx = build_reverse_index(wiki_dir)
    from_provenance = articles_affected_by(reverse_idx, changed_files)
    from_ripple = expand_ripple(ripple_rules, changed_files)
    slugs = from_provenance | from_ripple

    # Lookup priorities from manifest
    priority_by_slug = {spec.slug: spec.priority for spec in manifest}
    pairs: list[tuple[str, ArticlePriority]] = []
    for slug in slugs:
        if slug in priority_by_slug:
            pairs.append((slug, priority_by_slug[slug]))
        # Slugs not in manifest are dropped — we can't compile them

    sorted_slugs = priority_sort(pairs)
    return [
        QueueItem(slug=slug, priority=priority_by_slug[slug], trigger=trigger)
        for slug in sorted_slugs
    ]
