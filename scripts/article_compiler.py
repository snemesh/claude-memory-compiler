"""Pass 1: article compiler.

Takes a single ArticleSpec + resolved source files + glossary context and
produces (or updates) the article's body via an LLM call. Writes provenance
frontmatter atomically and emits a log.md entry.
"""
from __future__ import annotations

from pathlib import Path

from dataclasses import dataclass
from datetime import datetime, timezone

from articles_manifest import ArticleSpec
from llm_adapter import call_llm
from provenance import Provenance, SourceRef, write_provenance
from queue_builder import QueueItem
from source_resolver import build_source_refs, resolve_sources
from wiki_log import LogEntry, append_entry

_PER_FILE_MAX_CHARS = 30_000
_MODEL_PASS1 = "claude-sonnet-4-6"
_DEFAULT_MAX_TURNS = 30


@dataclass(frozen=True)
class CompileResult:
    slug: str
    cost_usd: float
    sources_count: int
    model: str


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
    feedback_issues: list | None = None,
) -> str:
    """Construct the Pass 1 prompt for the LLM.

    `source_contents` maps path → file body. Each file is truncated to
    _PER_FILE_MAX_CHARS to keep the prompt bounded.
    `feedback_issues` is an optional list of ValidationIssue objects
    from a prior Pass 3 run that must be addressed in this compile.
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

    if feedback_issues:
        lines.append("## Feedback from previous validation pass (must be addressed)")
        lines.append("")
        for issue in feedback_issues:
            lines.append(f"- **{issue.kind}**: {issue.description}")
            if issue.evidence:
                lines.append(f"  - evidence: {issue.evidence}")
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


def _read_article_body(article_path: Path) -> str | None:
    """Return body (sans frontmatter) if article exists, else None."""
    if not article_path.exists():
        return None
    content = article_path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            return content[end + 5:]
    return content


def compile_article(
    item: QueueItem,
    spec: ArticleSpec,
    repo_root: Path,
    wiki_dir: Path,
    glossary_block: str,
    log_file: Path | None = None,
    feedback_issues: list | None = None,
) -> CompileResult:
    """Run Pass 1 on a single article.

    1. Resolve source globs → files + SHAs.
    2. Read file contents (truncated).
    3. Read existing body (if any).
    4. Build prompt, call LLM (SDK will Write/Edit the file directly).
    5. Write provenance frontmatter (preserves body).
    6. If log_file is given, append an 'ingest' entry.
    """
    paths = resolve_sources(spec, repo_root)
    refs = build_source_refs(paths, repo_root)
    contents = {ref.path: (repo_root / ref.path).read_text(encoding="utf-8",
                                                            errors="ignore")
                for ref in refs}

    article_path = wiki_dir / f"{spec.slug}.md"
    existing_body = _read_article_body(article_path)

    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=refs,
        source_contents=contents,
        glossary_block=glossary_block,
        existing_body=existing_body,
        trigger=item.trigger,
        feedback_issues=feedback_issues,
    )

    response = call_llm(
        prompt=prompt,
        model=_MODEL_PASS1,
        cwd=wiki_dir,
        max_turns=_DEFAULT_MAX_TURNS,
    )

    if not article_path.exists():
        article_path.write_text(f"# {spec.slug}\n\n(LLM did not emit a body.)\n",
                                encoding="utf-8")

    now = datetime.now(tz=timezone.utc)
    prov = Provenance(
        sources=refs,
        compiled_at=now,
        model=response.model,
        cost_usd=response.cost_usd,
        triggered_by=item.trigger,
    )
    write_provenance(article_path, prov)

    if log_file is not None:
        append_entry(log_file, LogEntry(
            ts=now,
            event="ingest",
            summary=spec.slug,
            metadata={
                "cost_usd": round(response.cost_usd, 4),
                "sources": len(refs),
                "model": response.model,
                "trigger": item.trigger,
            },
        ))

    return CompileResult(
        slug=spec.slug,
        cost_usd=response.cost_usd,
        sources_count=len(refs),
        model=response.model,
    )
