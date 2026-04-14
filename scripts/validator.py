"""Pass 3: Haiku-based cross-article validator.

For each article compiled in Pass 1, prompt Haiku with the article body
plus a small set of related sibling articles (from ripple-map). Ask the
model to surface contradictions, terminology mismatches, or stale claims.
Results are structured JSON that the caller can act on (re-queue article
to Pass 1 with feedback, or just log).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from llm_adapter import call_llm

_ARTICLE_MAX_CHARS = 20_000
_OVERVIEW_MAX_CHARS = 8_000
_MODEL_PASS3 = "claude-haiku-4-5"
_MAX_TURNS = 5  # validation is a single-turn task


@dataclass(frozen=True)
class ValidationIssue:
    kind: str  # "contradiction" | "terminology" | "stale"
    description: str
    evidence: str


@dataclass(frozen=True)
class ValidationResult:
    slug: str
    issues: list[ValidationIssue]
    cost_usd: float
    model: str


def _read_article_body(wiki_dir: Path, slug: str, cap: int) -> str:
    path = wiki_dir / f"{slug}.md"
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            content = content[end + 5:]
    if len(content) <= cap:
        return content
    return content[:cap] + "\n... [truncated]"


def build_validation_prompt(
    article_slug: str,
    wiki_dir: Path,
    sibling_slugs: list[str],
) -> str:
    """Construct the Pass 3 Haiku prompt.

    Includes:
      - overview.md (invariants, bounded contexts)
      - the article under validation
      - each sibling article listed in sibling_slugs
      - explicit request for JSON-formatted issue list
    """
    overview = _read_article_body(wiki_dir, "overview", _OVERVIEW_MAX_CHARS)
    main = _read_article_body(wiki_dir, article_slug, _ARTICLE_MAX_CHARS)

    lines: list[str] = [
        f"# Validate wiki article: {article_slug}",
        "",
        "## Overview (canonical invariants)",
        "",
        overview or "(no overview.md available)",
        "",
        f"## Article under review: {article_slug}",
        "",
        main or "(article empty)",
        "",
    ]

    for sib in sibling_slugs:
        sib_body = _read_article_body(wiki_dir, sib, _ARTICLE_MAX_CHARS)
        if not sib_body:
            continue
        lines.extend([
            f"## Sibling article: {sib}",
            "",
            sib_body,
            "",
        ])

    lines.extend([
        "## Your task",
        "",
        "Review the article under validation. Identify:",
        "1. Contradictions with the overview or sibling articles",
        "2. Terminology mismatches (same concept named differently across articles)",
        "3. Stale claims (description that no longer matches the code/data shown)",
        "",
        "Respond with a JSON array of issue objects:",
        "",
        "```json",
        '[{"kind": "contradiction|terminology|stale", '
        '"description": "...", '
        '"evidence": "quote from article + sibling"}]',
        "```",
        "",
        "Return `[]` if no issues found. No prose outside the JSON.",
    ])

    return "\n".join(lines)


_JSON_BLOCK_RE = re.compile(r"```json\s*(\[.*?\])\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"^\s*(\[.*?\])\s*$", re.DOTALL)


def parse_validation_response(text: str) -> list[ValidationIssue]:
    """Extract the JSON array of issues from Haiku's response text.

    Tolerates preamble/postamble around a ```json fenced block, and also
    accepts a bare JSON array if no fence is present. Returns [] on any
    parsing failure — validation should never crash the pipeline.
    """
    match = _JSON_BLOCK_RE.search(text) or _BARE_JSON_RE.search(text)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    issues: list[ValidationIssue] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        issues.append(ValidationIssue(
            kind=str(item.get("kind", "")),
            description=str(item.get("description", "")),
            evidence=str(item.get("evidence", "")),
        ))
    return issues


def validate_article(
    article_slug: str,
    wiki_dir: Path,
    sibling_slugs: list[str],
) -> ValidationResult:
    """Run Pass 3 on a single article."""
    prompt = build_validation_prompt(
        article_slug=article_slug,
        wiki_dir=wiki_dir,
        sibling_slugs=sibling_slugs,
    )
    response = call_llm(
        prompt=prompt,
        model=_MODEL_PASS3,
        cwd=wiki_dir,
        max_turns=_MAX_TURNS,
    )
    issues = parse_validation_response(response.text)
    return ValidationResult(
        slug=article_slug,
        issues=issues,
        cost_usd=response.cost_usd,
        model=response.model,
    )
