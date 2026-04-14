"""Loader for config/wiki-articles.yaml — the wiki's article manifest."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from topo import ArticlePriority


@dataclass(frozen=True)
class ArticleSpec:
    slug: str
    priority: ArticlePriority
    sources: list[str]  # list of fnmatch globs relative to project root


def load_manifest(path: Path) -> list[ArticleSpec]:
    """Parse wiki-articles.yaml into a list of ArticleSpec.

    Raises FileNotFoundError if the file does not exist, and ValueError
    if any article declares an unknown priority tier.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: list[ArticleSpec] = []
    for item in raw.get("articles", []):
        priority_name = item["priority"]
        try:
            priority = ArticlePriority[priority_name]
        except KeyError as exc:
            raise ValueError(f"unknown priority tier: {priority_name}") from exc
        specs.append(ArticleSpec(
            slug=item["slug"],
            priority=priority,
            sources=list(item.get("sources", [])),
        ))
    return specs
