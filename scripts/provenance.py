"""Article provenance frontmatter.

Every compiled wiki article gets a YAML frontmatter block listing the source
files that produced it (with git-blob SHAs), compile metadata, and the trigger.
Used by the queue builder to compute reverse file→articles index.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceRef:
    path: str  # repo-relative
    sha: str   # short git-blob hash


@dataclass(frozen=True)
class Provenance:
    sources: list[SourceRef]
    compiled_at: datetime
    model: str
    cost_usd: float
    triggered_by: str  # "manual" | "ripple:<slug>" | "commit:<sha>" | "scheduled"


_FRONTMATTER_RE = re.compile(
    r"\A---\n(?P<body>.*?)\n---\n(?P<rest>.*)\Z",
    re.DOTALL,
)


def read_provenance(article: Path) -> Provenance | None:
    """Extract provenance from an article's YAML frontmatter. None if absent."""
    if not article.exists():
        return None
    content = article.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None
    try:
        data = yaml.safe_load(match["body"]) or {}
    except yaml.YAMLError:
        return None

    raw_sources = data.get("sources", [])
    # Ignore articles whose sources field doesn't match our shape
    # (e.g. existing wiki uses `sources: <int>` for source-document count).
    if not isinstance(raw_sources, list):
        return None
    try:
        sources = [SourceRef(path=s["path"], sha=s["sha"]) for s in raw_sources]
    except (TypeError, KeyError):
        return None
    compiled_at_raw = data.get("compiled_at")
    if isinstance(compiled_at_raw, str):
        compiled_at = datetime.fromisoformat(compiled_at_raw)
    elif isinstance(compiled_at_raw, datetime):
        compiled_at = compiled_at_raw
    else:
        return None

    return Provenance(
        sources=sources,
        compiled_at=compiled_at,
        model=str(data.get("model", "")),
        cost_usd=float(data.get("cost_usd", 0.0)),
        triggered_by=str(data.get("triggered_by", "")),
    )


def write_provenance(article: Path, prov: Provenance) -> None:
    """Write (or replace) the frontmatter block at the top of the article.

    Preserves the body content below the frontmatter.
    """
    content = article.read_text(encoding="utf-8") if article.exists() else ""
    match = _FRONTMATTER_RE.match(content)
    body = match["rest"] if match else content

    data = {
        "sources": [{"path": s.path, "sha": s.sha} for s in prov.sources],
        "compiled_at": prov.compiled_at.isoformat(),
        "model": prov.model,
        "cost_usd": prov.cost_usd,
        "triggered_by": prov.triggered_by,
    }
    fm = yaml.safe_dump(data, sort_keys=False, default_flow_style=False).rstrip()
    article.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
