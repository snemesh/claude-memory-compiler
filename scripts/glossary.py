"""Pinned glossary context for Pass 1 compile prompts (E3 enhancement).

Every compile call gets a small (~3-10KB) block containing:
  - Truncated overview.md (canonical invariants, bounded contexts)
  - index.md preview (first ~40 lines)
  - Explicit list of known wiki slugs so the LLM links them correctly

This prevents vocabulary drift across per-article compiles.
"""
from __future__ import annotations

from pathlib import Path

_OVERVIEW_MAX_CHARS = 10_000
_INDEX_MAX_LINES = 40


def _read_capped(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n... [truncated]"


def _read_index_preview(path: Path, max_lines: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()[:max_lines]
    return "\n".join(lines)


def build_glossary_block(wiki_dir: Path, known_slugs: list[str]) -> str:
    """Assemble the pinned context block: overview + index preview + slug list."""
    overview = _read_capped(wiki_dir / "overview.md", _OVERVIEW_MAX_CHARS)
    index_preview = _read_index_preview(wiki_dir / "index.md", _INDEX_MAX_LINES)
    slug_list = ", ".join(f"[[{s}]]" for s in sorted(known_slugs))

    parts: list[str] = ["## Pinned project context (glossary)"]
    if overview:
        parts.append(f"\n### Overview\n\n{overview}")
    if index_preview:
        parts.append(f"\n### Index preview\n\n{index_preview}")
    if slug_list:
        parts.append(
            f"\n### Known wiki slugs (link them with `[[slug]]` where relevant)\n\n"
            f"{slug_list}"
        )
    return "\n".join(parts)
