"""Pipeline orchestration: run Pass 1 + Pass 2 + Pass 3 over a queue.

Order of operations:
  1. Build glossary block once (cheap, read-only from wiki state at start).
  2. For each QueueItem: Pass 1 compile (writes article + provenance + log).
  3. After all Pass 1 done: Pass 2 cross-link across the whole wiki.
  4. Pass 3 Haiku validate for each compiled article (read-only; logs issues).

This is intentionally linear and synchronous — the queue is already in
dependency-safe topological order, so no concurrency is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from article_compiler import compile_article
from articles_manifest import ArticleSpec
from crosslink import run_pass2
from glossary import build_glossary_block
from queue_builder import QueueItem
from ripple_map import RippleRule, expand_ripple
from validator import ValidationResult, validate_article
from wiki_log import LogEntry, append_entry


@dataclass(frozen=True)
class PipelineReport:
    articles_compiled: int
    total_cost_usd: float
    links_added: int
    dangling_refs: dict[str, list[str]]
    validation_results: list[ValidationResult]


def _siblings_for(
    slug: str,
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
) -> list[str]:
    """Pick a small list of related article slugs for Pass 3 context.

    Heuristic: apply ripple_rules to the slug's own sources globs, union
    all revalidate targets, drop self.
    """
    spec = next((s for s in manifest if s.slug == slug), None)
    if spec is None:
        return []
    siblings: set[str] = set()
    siblings |= expand_ripple(ripple_rules, spec.sources)
    siblings.discard(slug)
    return sorted(siblings)


def run_pipeline(
    queue: list[QueueItem],
    manifest: list[ArticleSpec],
    ripple_rules: list[RippleRule],
    repo_root: Path,
    wiki_dir: Path,
    log_file: Path | None = None,
) -> PipelineReport:
    """Run the full three-pass pipeline over a compile queue."""
    if not queue:
        return PipelineReport(
            articles_compiled=0,
            total_cost_usd=0.0,
            links_added=0,
            dangling_refs={},
            validation_results=[],
        )

    known_slugs = [s.slug for s in manifest]
    glossary = build_glossary_block(wiki_dir, known_slugs=known_slugs)
    spec_by_slug = {s.slug: s for s in manifest}

    total_cost = 0.0
    compiled = 0

    # ── Pass 1 ────────────────────────────────────────────────────
    for item in queue:
        spec = spec_by_slug.get(item.slug)
        if spec is None:
            continue
        result = compile_article(
            item=item,
            spec=spec,
            repo_root=repo_root,
            wiki_dir=wiki_dir,
            glossary_block=glossary,
            log_file=log_file,
        )
        total_cost += result.cost_usd
        compiled += 1

    # ── Pass 2 ────────────────────────────────────────────────────
    pass2 = run_pass2(wiki_dir=wiki_dir, known_slugs=known_slugs)
    if log_file is not None:
        append_entry(log_file, LogEntry(
            ts=datetime.now(tz=timezone.utc),
            event="lint",
            summary=f"crosslink: {pass2.links_added} added",
            metadata={"dangling": len(pass2.dangling)},
        ))

    # ── Pass 3 ────────────────────────────────────────────────────
    validations: list[ValidationResult] = []
    for item in queue:
        siblings = _siblings_for(item.slug, manifest, ripple_rules)
        result = validate_article(
            article_slug=item.slug,
            wiki_dir=wiki_dir,
            sibling_slugs=siblings,
        )
        validations.append(result)
        total_cost += result.cost_usd
        if log_file is not None and result.issues:
            append_entry(log_file, LogEntry(
                ts=datetime.now(tz=timezone.utc),
                event="lint",
                summary=f"validate {item.slug}: {len(result.issues)} issues",
                metadata={"kinds": ",".join(i.kind for i in result.issues)},
            ))

    return PipelineReport(
        articles_compiled=compiled,
        total_cost_usd=total_cost,
        links_added=pass2.links_added,
        dangling_refs=pass2.dangling,
        validation_results=validations,
    )
