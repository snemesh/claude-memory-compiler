"""Resolve ArticleSpec.sources (globs) → concrete list of Path under repo.

Uses pathlib.Path.glob with ** recursive semantics. Results are
deduplicated and sorted alphabetically for determinism across runs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from articles_manifest import ArticleSpec
from provenance import SourceRef


def resolve_sources(spec: ArticleSpec, repo_root: Path) -> list[Path]:
    """Expand every glob in spec.sources; return unique sorted file paths."""
    seen: set[Path] = set()
    for pattern in spec.sources:
        for match in repo_root.glob(pattern):
            if match.is_file():
                seen.add(match.resolve())
    return sorted(seen)


def build_source_refs(paths: list[Path], repo_root: Path) -> list[SourceRef]:
    """Convert absolute file paths to SourceRef with repo-relative path + SHA.

    SHA is the first 16 hex chars of sha256(content). This is a short blob
    identifier — not git's actual blob hash, but stable, fast, and equivalent
    for change detection.
    """
    refs: list[SourceRef] = []
    for path in paths:
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()[:16]
        rel = path.resolve().relative_to(repo_root.resolve())
        refs.append(SourceRef(path=str(rel), sha=sha))
    return refs
