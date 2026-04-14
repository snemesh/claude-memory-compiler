"""Queue builder: changed files → ordered list of QueueItem for compile pipeline.

Sources of article candidates:
1. Reverse provenance index — articles that literally list a changed file as a source
2. Ripple map — articles declared via fnmatch patterns in ripple-map.yaml

Union both, deduplicate, sort by priority tier.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from articles_manifest import ArticleSpec
from reverse_index import articles_affected_by, build_reverse_index
from ripple_map import RippleRule, expand_ripple
from topo import ArticlePriority, priority_sort


def _matches_pattern(path: str, pattern: str) -> bool:
    """Gitignore-style match: '**/' means zero-or-more path segments.

    Python's fnmatch requires '**/' to match at least one segment (since its
    '*' already crosses '/' greedily, '**/X' becomes '*/X' which requires a
    literal '/'). We additionally try the pattern with '/**/' collapsed to
    '/' so top-level files under a prefix also match.
    """
    if fnmatch.fnmatch(path, pattern):
        return True
    if "/**/" in pattern:
        collapsed = pattern.replace("/**/", "/")
        if fnmatch.fnmatch(path, collapsed):
            return True
    return False


def _articles_from_manifest_globs(
    changed_files: list[str],
    manifest: list[ArticleSpec],
) -> set[str]:
    """Match changed files against each ArticleSpec's sources globs.

    Covers the first-compile case: no provenance exists yet, so
    reverse_index is empty, but the manifest declares which articles
    own which file patterns.
    """
    affected: set[str] = set()
    for spec in manifest:
        for pattern in spec.sources:
            for path in changed_files:
                if _matches_pattern(path, pattern):
                    affected.add(spec.slug)
                    break
    return affected


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
    from_manifest = _articles_from_manifest_globs(changed_files, manifest)
    slugs = from_provenance | from_ripple | from_manifest

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
