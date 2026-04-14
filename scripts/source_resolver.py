"""Resolve ArticleSpec.sources (globs) → concrete list of Path under repo.

Uses pathlib.Path.glob with ** recursive semantics. Results are
deduplicated and sorted alphabetically for determinism across runs.
"""
from __future__ import annotations

from pathlib import Path

from articles_manifest import ArticleSpec


def resolve_sources(spec: ArticleSpec, repo_root: Path) -> list[Path]:
    """Expand every glob in spec.sources; return unique sorted file paths."""
    seen: set[Path] = set()
    for pattern in spec.sources:
        for match in repo_root.glob(pattern):
            if match.is_file():
                seen.add(match.resolve())
    return sorted(seen)
