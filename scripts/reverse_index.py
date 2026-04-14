"""Reverse index: source-file path → set of article slugs that depend on it.

Built by scanning all *.md articles in the wiki root and reading each one's
provenance frontmatter. The queue builder uses this to translate a commit's
changed-file list into the set of articles that need recompilation.
"""
from __future__ import annotations

from pathlib import Path

from provenance import read_provenance


def build_reverse_index(wiki_dir: Path) -> dict[str, set[str]]:
    """Scan wiki_dir for articles and return {source_path: {article_slug, ...}}.

    Articles without provenance frontmatter are silently skipped — they have
    no declared dependencies and cannot be invalidated by file changes.
    """
    index: dict[str, set[str]] = {}
    if not wiki_dir.exists():
        return index
    for article_path in wiki_dir.rglob("*.md"):
        if article_path.name in {"index.md", "log.md", "overview.md"}:
            continue
        prov = read_provenance(article_path)
        if prov is None:
            continue
        slug = article_path.stem
        for src in prov.sources:
            index.setdefault(src.path, set()).add(slug)
    return index


def articles_affected_by(
    index: dict[str, set[str]],
    changed_files: list[str],
) -> set[str]:
    """Union of articles referenced by any of the changed files."""
    affected: set[str] = set()
    for path in changed_files:
        affected |= index.get(path, set())
    return affected
