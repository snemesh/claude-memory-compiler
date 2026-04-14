"""Ripple map: declared non-obvious cross-service dependencies.

Seeds the queue with articles that must be revalidated when certain files
change, even if those articles do not list those files in their provenance.
Used for shared DB tables, Kafka topics, event contracts, etc.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RippleRule:
    match: str  # fnmatch-compatible glob, e.g. "proto/clubs/**"
    revalidate: list[str]  # list of article slugs


def load_ripple_map(path: Path) -> list[RippleRule]:
    """Load ripple rules from YAML; empty list if file missing or invalid."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    patterns = data.get("patterns", [])
    return [
        RippleRule(
            match=p["match"],
            revalidate=list(p.get("revalidate", [])),
        )
        for p in patterns
        if "match" in p
    ]


def expand_ripple(rules: list[RippleRule], changed_files: list[str]) -> set[str]:
    """For each changed file, find all matching rules and union their
    revalidate lists."""
    affected: set[str] = set()
    for file in changed_files:
        for rule in rules:
            if fnmatch.fnmatch(file, rule.match):
                affected.update(rule.revalidate)
    return affected
