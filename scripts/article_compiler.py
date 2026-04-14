"""Pass 1: article compiler.

Takes a single ArticleSpec + resolved source files + glossary context and
produces (or updates) the article's body via an LLM call. Writes provenance
frontmatter atomically and emits a log.md entry.
"""
from __future__ import annotations

from pathlib import Path

from articles_manifest import ArticleSpec
from provenance import SourceRef

_PER_FILE_MAX_CHARS = 30_000


def _truncate(content: str, cap: int) -> str:
    if len(content) <= cap:
        return content
    return content[:cap] + f"\n... [file truncated — {len(content) - cap} chars omitted]"


def build_pass1_prompt(
    spec: ArticleSpec,
    source_refs: list[SourceRef],
    source_contents: dict[str, str],
    glossary_block: str,
    existing_body: str | None,
    trigger: str,
) -> str:
    """Construct the Pass 1 prompt for the LLM.

    `source_contents` maps path → file body. Each file is truncated to
    _PER_FILE_MAX_CHARS to keep the prompt bounded.
    """
    mode = "update" if existing_body else "create"
    lines: list[str] = [
        f"# Compile wiki article: {spec.slug}",
        "",
        f"**Priority tier:** {spec.priority.name}",
        f"**Mode:** {mode} (trigger: {trigger})",
        "",
    ]

    if glossary_block:
        lines.append(glossary_block)
        lines.append("")

    if existing_body is not None:
        lines.append("## Existing article body (to refine)")
        lines.append("")
        lines.append(_truncate(existing_body, _PER_FILE_MAX_CHARS))
        lines.append("")

    lines.append("## Source files")
    lines.append("")
    for ref in source_refs:
        content = source_contents.get(ref.path, "")
        lines.append(f"### `{ref.path}` (sha:{ref.sha})")
        lines.append("")
        lines.append("```")
        lines.append(_truncate(content, _PER_FILE_MAX_CHARS))
        lines.append("```")
        lines.append("")

    lines.append("## Your task")
    lines.append("")
    lines.append(
        f"{'Create' if mode == 'create' else 'Update'} the article at the "
        f"wiki location for slug `{spec.slug}`. Link to other wiki pages via "
        f"`[[slug]]` where relevant (see pinned slug list above). Use "
        f"encyclopedia tone: neutral, third-person, complete. Focus on the "
        f"information present in the source files; do not invent."
    )

    return "\n".join(lines)
