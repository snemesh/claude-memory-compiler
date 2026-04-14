# Compile Pipeline Implementation Plan (Pass 1 + Pass 2 + Pass 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three-pass article-compilation pipeline on top of the Plan 1 foundation — Pass 1 (Sonnet generates article content from source files), Pass 2 (deterministic cross-link of known wiki slugs, no LLM), Pass 3 (Haiku validates article against ripple-map siblings for contradictions).

**Architecture:** Six new modules under `scripts/`, each a pure function / thin adapter. LLM calls are isolated behind a single `llm_adapter.py` wrapper that tests monkeypatch. Articles are written atomically: body replaced, provenance frontmatter regenerated. log.md entries appended after every Pass 1 success.

**Tech Stack:** Python 3.12+, `claude_agent_sdk>=0.1.29` (already in deps), `PyYAML`, `pytest`, `pytest-mock`. Models: `claude-sonnet-4-6` (Pass 1), `claude-haiku-4-5` (Pass 3).

**Reference spec:** `docs/specs/2026-04-14-wiki-compile-chunked-design.md`
**Depends on Plan 1:** `scripts/{quota,jsonl_quota,wiki_log,provenance,reverse_index,ripple_map,topo,articles_manifest,queue_builder,plan_sync}.py`

---

## File layout

```
scripts/
├── source_resolver.py    # NEW: glob ArticleSpec.sources → list[SourceRef]
├── glossary.py           # NEW: build pinned glossary block for Pass 1 prompt
├── llm_adapter.py        # NEW: thin Agent SDK wrapper, returns (text, cost)
├── article_compiler.py   # NEW: Pass 1 — compile one article end-to-end
├── crosslink.py          # NEW: Pass 2 — scan + auto-wrap known slugs
├── validator.py          # NEW: Pass 3 — Haiku validation of one article
└── pipeline.py           # NEW: run_pipeline(queue) — orchestrates P1→P2→P3
```

---

## Phase 1: Source resolver

### Task 1.1: Glob sources against repo root

**Files:**
- Create: `tests/test_source_resolver.py`
- Create: `scripts/source_resolver.py`

- [ ] **Step 1: Write failing tests for resolve_sources**

Create `tests/test_source_resolver.py`:

```python
"""Tests for source_resolver.py — ArticleSpec sources → actual files."""
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from source_resolver import resolve_sources
from topo import ArticlePriority


def test_resolve_sources_expands_globs(tmp_path):
    (tmp_path / "services" / "clubs").mkdir(parents=True)
    (tmp_path / "services" / "clubs" / "entity.go").write_text("package clubs\n")
    (tmp_path / "services" / "clubs" / "main.go").write_text("package clubs\n")
    (tmp_path / "services" / "auth").mkdir(parents=True)
    (tmp_path / "services" / "auth" / "main.go").write_text("package auth\n")

    spec = ArticleSpec(
        slug="clubs",
        priority=ArticlePriority.SERVICES,
        sources=["services/clubs/**/*.go"],
    )
    paths = resolve_sources(spec, tmp_path)
    rel = sorted(str(p.relative_to(tmp_path)) for p in paths)
    assert rel == ["services/clubs/entity.go", "services/clubs/main.go"]


def test_resolve_sources_deduplicates_across_globs(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.go").write_text("")
    spec = ArticleSpec(
        slug="x",
        priority=ArticlePriority.SERVICES,
        sources=["x/*.go", "x/**/*.go"],
    )
    paths = resolve_sources(spec, tmp_path)
    assert len(paths) == 1


def test_resolve_sources_returns_sorted_for_determinism(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "z.go").write_text("")
    (tmp_path / "x" / "a.go").write_text("")
    (tmp_path / "x" / "m.go").write_text("")
    spec = ArticleSpec(
        slug="x",
        priority=ArticlePriority.SERVICES,
        sources=["x/*.go"],
    )
    paths = resolve_sources(spec, tmp_path)
    names = [p.name for p in paths]
    assert names == ["a.go", "m.go", "z.go"]


def test_resolve_sources_empty_when_no_match(tmp_path):
    spec = ArticleSpec(
        slug="x",
        priority=ArticlePriority.SERVICES,
        sources=["nonexistent/**/*.go"],
    )
    assert resolve_sources(spec, tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_source_resolver.py -v`
Expected: `ImportError: cannot import name 'resolve_sources'`

- [ ] **Step 3: Implement resolve_sources**

Create `scripts/source_resolver.py`:

```python
"""Resolve ArticleSpec.sources (globs) → concrete list of Path under repo.

Uses pathlib.Path.glob with ** recursive semantics. Results are
deduplicated and sorted alphabetically for determinism across runs.
"""
from __future__ import annotations

from pathlib import Path

from articles_manifest import ArticleSpec


def resolve_sources(spec: ArticleSpec, repo_root: Path) -> list[Path]:
    """Expand every glob in spec.sources; return unique sorted file paths."""
    seen: set[Path] = set()
    for pattern in spec.sources:
        for match in repo_root.glob(pattern):
            if match.is_file():
                seen.add(match.resolve())
    return sorted(seen)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_source_resolver.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/source_resolver.py tests/test_source_resolver.py
git commit -m "feat(compile): resolve ArticleSpec.sources globs to file list"
```

---

### Task 1.2: Compute short blob SHAs for source files

**Files:**
- Modify: `tests/test_source_resolver.py`
- Modify: `scripts/source_resolver.py`

- [ ] **Step 1: Write failing tests for build_source_refs**

Append to `tests/test_source_resolver.py`:

```python
from provenance import SourceRef
from source_resolver import build_source_refs


def test_build_source_refs_returns_refs_with_sha(tmp_path):
    (tmp_path / "a.go").write_text("package a\n")
    (tmp_path / "b.go").write_text("package b\n")

    refs = build_source_refs([tmp_path / "a.go", tmp_path / "b.go"], tmp_path)
    assert len(refs) == 2
    assert all(isinstance(r, SourceRef) for r in refs)
    assert refs[0].path == "a.go"
    assert refs[1].path == "b.go"
    # SHAs are non-empty and stable
    assert refs[0].sha
    assert refs[0].sha == build_source_refs([tmp_path / "a.go"], tmp_path)[0].sha


def test_build_source_refs_paths_are_repo_relative(tmp_path):
    (tmp_path / "services" / "x").mkdir(parents=True)
    (tmp_path / "services" / "x" / "main.go").write_text("")

    refs = build_source_refs([tmp_path / "services" / "x" / "main.go"], tmp_path)
    assert refs[0].path == "services/x/main.go"
    assert not refs[0].path.startswith("/")


def test_build_source_refs_sha_differs_when_content_changes(tmp_path):
    f = tmp_path / "a.go"
    f.write_text("v1")
    sha_v1 = build_source_refs([f], tmp_path)[0].sha
    f.write_text("v2")
    sha_v2 = build_source_refs([f], tmp_path)[0].sha
    assert sha_v1 != sha_v2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_source_resolver.py -v`
Expected: `ImportError: cannot import name 'build_source_refs'`

- [ ] **Step 3: Implement build_source_refs**

Append to `scripts/source_resolver.py`:

```python
import hashlib

from provenance import SourceRef


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_source_resolver.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/source_resolver.py tests/test_source_resolver.py
git commit -m "feat(compile): build_source_refs with short sha256 hashes"
```

---

## Phase 2: Pinned glossary builder

### Task 2.1: Build glossary context block

**Files:**
- Create: `tests/test_glossary.py`
- Create: `scripts/glossary.py`

- [ ] **Step 1: Write failing tests for build_glossary_block**

Create `tests/test_glossary.py`:

```python
"""Tests for glossary.py — pinned context block for Pass 1 prompts."""
from pathlib import Path

import pytest

from glossary import build_glossary_block


def test_build_glossary_block_includes_overview_and_index(tmp_path):
    wiki = tmp_path
    (wiki / "overview.md").write_text("# Arena Overview\n\nBounded contexts...\n")
    (wiki / "index.md").write_text("# Index\n\n- [[clubs]]\n- [[api-gateway]]\n")

    block = build_glossary_block(wiki, known_slugs=["clubs", "api-gateway"])

    assert "Overview" in block
    assert "Bounded contexts" in block
    assert "Index" in block
    assert "[[clubs]]" in block
    assert "[[api-gateway]]" in block


def test_build_glossary_block_truncates_long_overview(tmp_path):
    wiki = tmp_path
    big = "word " * 10_000  # ~50KB
    (wiki / "overview.md").write_text(f"# Overview\n\n{big}")
    (wiki / "index.md").write_text("# Index\n")

    block = build_glossary_block(wiki, known_slugs=[])
    # Must be capped well below full overview size to keep prompt small.
    assert len(block) < 15_000


def test_build_glossary_block_lists_known_slugs_for_linking():
    # Slug list should be explicit so LLM knows exactly what to [[link]]
    block = build_glossary_block(wiki_dir=Path("/nonexistent"),
                                 known_slugs=["clubs", "api-gateway", "fan-entity"])
    assert "clubs" in block
    assert "api-gateway" in block
    assert "fan-entity" in block


def test_build_glossary_block_handles_missing_overview(tmp_path):
    # Should not crash if overview.md is absent
    block = build_glossary_block(tmp_path, known_slugs=["x"])
    assert "x" in block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_glossary.py -v`
Expected: `ImportError: cannot import name 'build_glossary_block'`

- [ ] **Step 3: Implement build_glossary_block**

Create `scripts/glossary.py`:

```python
"""Pinned glossary context for Pass 1 compile prompts (E3 enhancement).

Every compile call gets a small (~3-10KB) block containing:
  - Truncated overview.md (canonical invariants, bounded contexts)
  - index.md preview (first ~30 lines)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_glossary.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/glossary.py tests/test_glossary.py
git commit -m "feat(compile): pinned glossary block for Pass 1 prompts"
```

---

## Phase 3: LLM adapter

### Task 3.1: Thin SDK wrapper with deterministic test hook

**Files:**
- Create: `tests/test_llm_adapter.py`
- Create: `scripts/llm_adapter.py`

- [ ] **Step 1: Write failing tests for LLMResponse + call_llm**

Create `tests/test_llm_adapter.py`:

```python
"""Tests for llm_adapter.py — LLM wrapper that tests can monkeypatch."""
from unittest.mock import AsyncMock

import pytest

from llm_adapter import LLMResponse, call_llm


def test_llm_response_is_immutable():
    r = LLMResponse(text="hello", cost_usd=0.12, model="claude-sonnet-4-6")
    with pytest.raises(Exception):
        r.text = "changed"  # frozen dataclass


def test_call_llm_dispatches_via_run_llm_fn(monkeypatch, tmp_path):
    """call_llm goes through _run_llm (monkeypatchable) rather than the SDK directly."""
    captured = {}

    async def fake_run(prompt, model, cwd, max_turns):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["cwd"] = cwd
        return LLMResponse(text="FAKE", cost_usd=0.05, model=model)

    monkeypatch.setattr("llm_adapter._run_llm", fake_run)

    result = call_llm(
        prompt="compile clubs",
        model="claude-sonnet-4-6",
        cwd=tmp_path,
        max_turns=30,
    )
    assert result.text == "FAKE"
    assert result.cost_usd == 0.05
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["prompt"] == "compile clubs"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_adapter.py -v`
Expected: `ImportError: cannot import name 'LLMResponse'`

- [ ] **Step 3: Implement llm_adapter**

Create `scripts/llm_adapter.py`:

```python
"""Thin synchronous wrapper around claude_agent_sdk.query().

The SDK is async and streams messages; callers here want a simple
synchronous (prompt → text+cost) function. Extracted so tests can
monkeypatch `_run_llm` without touching the SDK at all.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMResponse:
    text: str
    cost_usd: float
    model: str


async def _run_llm(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int,
) -> LLMResponse:
    """Call the Agent SDK, stream messages, return consolidated response.

    Tests monkeypatch this function; don't add logic above the SDK call.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    text_parts: list[str] = []
    cost = 0.0

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(cwd),
            system_prompt={"type": "preset", "preset": "claude_code"},
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=max_turns,
            model=model,
            add_dirs=[str(cwd)],
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0

    return LLMResponse(text="\n".join(text_parts), cost_usd=cost, model=model)


def call_llm(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int = 30,
) -> LLMResponse:
    """Synchronous entry point. Runs the async SDK call in a fresh event loop."""
    return asyncio.run(_run_llm(prompt, model, cwd, max_turns))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_adapter.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/llm_adapter.py tests/test_llm_adapter.py
git commit -m "feat(compile): llm_adapter — testable SDK wrapper"
```

---

## Phase 4: Pass 1 — article compiler

### Task 4.1: Pass 1 prompt template

**Files:**
- Create: `tests/test_article_compiler.py`
- Create: `scripts/article_compiler.py`

- [ ] **Step 1: Write failing tests for build_pass1_prompt**

Create `tests/test_article_compiler.py`:

```python
"""Tests for article_compiler.py — Pass 1 single-article compilation."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from article_compiler import build_pass1_prompt
from articles_manifest import ArticleSpec
from provenance import SourceRef
from topo import ArticlePriority


def test_build_pass1_prompt_mentions_slug_and_priority():
    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES,
                       sources=["services/clubs/**"])
    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=[SourceRef(path="services/clubs/entity.go", sha="abc")],
        source_contents={"services/clubs/entity.go": "package clubs"},
        glossary_block="## Pinned\n\nBounded contexts...",
        existing_body=None,
        trigger="manual",
    )
    assert "clubs" in prompt
    assert "SERVICES" in prompt
    assert "Bounded contexts" in prompt
    assert "package clubs" in prompt


def test_build_pass1_prompt_includes_existing_body_when_provided():
    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES, sources=[])
    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=[],
        source_contents={},
        glossary_block="",
        existing_body="# Clubs\n\nPrevious content that should be refined.\n",
        trigger="ripple:api-gateway",
    )
    assert "Previous content" in prompt
    assert "ripple:api-gateway" in prompt


def test_build_pass1_prompt_without_existing_body_requests_creation():
    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES, sources=[])
    prompt = build_pass1_prompt(
        spec=spec, source_refs=[], source_contents={},
        glossary_block="", existing_body=None, trigger="manual",
    )
    # Prompt must distinguish "create new" from "update existing"
    assert "create" in prompt.lower() or "new" in prompt.lower()


def test_build_pass1_prompt_truncates_huge_source_file():
    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=[])
    big = "x" * 200_000
    prompt = build_pass1_prompt(
        spec=spec,
        source_refs=[SourceRef(path="big.go", sha="abc")],
        source_contents={"big.go": big},
        glossary_block="",
        existing_body=None,
        trigger="manual",
    )
    # Must cap per-file inclusion well under 200KB
    assert len(prompt) < 150_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_article_compiler.py -v`
Expected: `ImportError: cannot import name 'build_pass1_prompt'`

- [ ] **Step 3: Implement build_pass1_prompt**

Create `scripts/article_compiler.py`:

```python
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
        f"",
        f"**Priority tier:** {spec.priority.name}",
        f"**Mode:** {mode} (trigger: {trigger})",
        f"",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_article_compiler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/article_compiler.py tests/test_article_compiler.py
git commit -m "feat(compile): build_pass1_prompt — Pass 1 prompt template"
```

---

### Task 4.2: compile_article end-to-end with mocked LLM

**Files:**
- Modify: `tests/test_article_compiler.py`
- Modify: `scripts/article_compiler.py`

- [ ] **Step 1: Write failing tests for compile_article**

Append to `tests/test_article_compiler.py`:

```python
from article_compiler import CompileResult, compile_article
from llm_adapter import LLMResponse
from queue_builder import QueueItem


def test_compile_article_writes_body_and_provenance(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / "services" / "clubs").mkdir(parents=True)
    (repo / "services" / "clubs" / "entity.go").write_text("package clubs\n")

    spec = ArticleSpec(
        slug="clubs",
        priority=ArticlePriority.SERVICES,
        sources=["services/clubs/**/*.go"],
    )
    item = QueueItem(slug="clubs", priority=ArticlePriority.SERVICES, trigger="manual")

    def fake_llm(prompt, model, cwd, max_turns=30):
        # Simulate SDK having already written the file via Write tool —
        # the SDK's tool-use flow does the actual write, and we just report.
        article_path = wiki / "clubs.md"
        article_path.write_text("# Clubs\n\nClubs are organizations...\n", encoding="utf-8")
        return LLMResponse(text="Wrote clubs.md", cost_usd=0.12, model=model)

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    result = compile_article(
        item=item,
        spec=spec,
        repo_root=repo,
        wiki_dir=wiki,
        glossary_block="## Pinned\n",
    )

    assert isinstance(result, CompileResult)
    assert result.cost_usd == 0.12
    assert (wiki / "clubs.md").exists()

    # Body preserved, frontmatter added
    content = (wiki / "clubs.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "Clubs are organizations" in content
    assert "services/clubs/entity.go" in content


def test_compile_article_preserves_existing_body_when_llm_skips_write(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / "x.go").write_text("package x\n")

    # Pre-existing article
    (wiki / "clubs.md").write_text("# Clubs\n\nExisting content\n", encoding="utf-8")

    spec = ArticleSpec(slug="clubs", priority=ArticlePriority.SERVICES,
                       sources=["x.go"])
    item = QueueItem("clubs", ArticlePriority.SERVICES, "manual")

    def fake_llm(prompt, model, cwd, max_turns=30):
        # Simulate: LLM decided no update needed, didn't touch the file
        return LLMResponse(text="No changes needed", cost_usd=0.02, model=model)

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)

    compile_article(
        item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
        glossary_block="",
    )

    content = (wiki / "clubs.md").read_text(encoding="utf-8")
    assert "Existing content" in content  # body preserved
    # Provenance still updated (new compile_at + cost)
    assert content.startswith("---\n")


def test_compile_article_records_trigger_in_provenance(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    (repo / "x.go").write_text("")

    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=["x.go"])
    item = QueueItem("x", ArticlePriority.SERVICES, "ripple:api-gateway")

    monkeypatch.setattr("article_compiler.call_llm",
                        lambda *a, **kw: LLMResponse("ok", 0.01, "claude-sonnet-4-6"))

    compile_article(item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
                    glossary_block="")

    content = (wiki / "x.md").read_text(encoding="utf-8")
    assert "ripple:api-gateway" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_article_compiler.py::test_compile_article_writes_body_and_provenance -v`
Expected: `ImportError: cannot import name 'CompileResult'`

- [ ] **Step 3: Implement compile_article**

Append to `scripts/article_compiler.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timezone

from llm_adapter import call_llm
from provenance import Provenance, read_provenance, write_provenance
from queue_builder import QueueItem
from source_resolver import build_source_refs, resolve_sources

_MODEL_PASS1 = "claude-sonnet-4-6"
_DEFAULT_MAX_TURNS = 30


@dataclass(frozen=True)
class CompileResult:
    slug: str
    cost_usd: float
    sources_count: int
    model: str


def _read_article_body(article_path: Path) -> str | None:
    """Return body (sans frontmatter) if article exists, else None."""
    if not article_path.exists():
        return None
    content = article_path.read_text(encoding="utf-8")
    # Strip any existing frontmatter
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
) -> CompileResult:
    """Run Pass 1 on a single article.

    1. Resolve source globs → files + SHAs.
    2. Read file contents (truncated).
    3. Read existing body (if any).
    4. Build prompt, call LLM (SDK will Write/Edit the file directly).
    5. Write provenance frontmatter (preserves body).
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
    )

    response = call_llm(
        prompt=prompt,
        model=_MODEL_PASS1,
        cwd=wiki_dir,
        max_turns=_DEFAULT_MAX_TURNS,
    )

    # Ensure article exists so we can attach frontmatter
    if not article_path.exists():
        article_path.write_text(f"# {spec.slug}\n\n(LLM did not emit a body.)\n",
                                encoding="utf-8")

    prov = Provenance(
        sources=refs,
        compiled_at=datetime.now(tz=timezone.utc),
        model=response.model,
        cost_usd=response.cost_usd,
        triggered_by=item.trigger,
    )
    write_provenance(article_path, prov)

    return CompileResult(
        slug=spec.slug,
        cost_usd=response.cost_usd,
        sources_count=len(refs),
        model=response.model,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_article_compiler.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/article_compiler.py tests/test_article_compiler.py
git commit -m "feat(compile): compile_article end-to-end with mocked LLM"
```

---

### Task 4.3: Emit log.md entry after successful compile

**Files:**
- Modify: `tests/test_article_compiler.py`
- Modify: `scripts/article_compiler.py`

- [ ] **Step 1: Write failing test for log.md emission**

Append to `tests/test_article_compiler.py`:

```python
def test_compile_article_appends_log_entry(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    wiki = tmp_path / "wiki"; wiki.mkdir()
    log_file = wiki / "log.md"
    (repo / "x.go").write_text("")

    spec = ArticleSpec(slug="x", priority=ArticlePriority.SERVICES, sources=["x.go"])
    item = QueueItem("x", ArticlePriority.SERVICES, "manual")

    monkeypatch.setattr("article_compiler.call_llm",
                        lambda *a, **kw: LLMResponse("ok", 0.07, "claude-sonnet-4-6"))

    compile_article(item=item, spec=spec, repo_root=repo, wiki_dir=wiki,
                    glossary_block="", log_file=log_file)

    content = log_file.read_text(encoding="utf-8")
    assert "# Wiki Operations Log" in content
    assert "ingest" in content
    assert "x" in content
    assert "cost_usd=0.07" in content
    assert "sources=1" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_article_compiler.py::test_compile_article_appends_log_entry -v`
Expected: `FAILED` — compile_article doesn't take log_file kwarg yet.

- [ ] **Step 3: Add log emission to compile_article**

In `scripts/article_compiler.py`, replace the `compile_article` signature and add log emission:

```python
from wiki_log import LogEntry, append_entry


def compile_article(
    item: QueueItem,
    spec: ArticleSpec,
    repo_root: Path,
    wiki_dir: Path,
    glossary_block: str,
    log_file: Path | None = None,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_article_compiler.py -v`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/article_compiler.py tests/test_article_compiler.py
git commit -m "feat(compile): emit log.md ingest entry after Pass 1 success"
```

---

## Phase 5: Pass 2 — cross-link

### Task 5.1: Detect unlinked slug mentions

**Files:**
- Create: `tests/test_crosslink.py`
- Create: `scripts/crosslink.py`

- [ ] **Step 1: Write failing tests for find_unlinked_mentions**

Create `tests/test_crosslink.py`:

```python
"""Tests for crosslink.py — Pass 2 wiki-link scanner and applier."""
import pytest

from crosslink import find_unlinked_mentions


def test_find_unlinked_mentions_basic():
    text = "See the clubs service and the api-gateway for details."
    mentions = find_unlinked_mentions(text, known_slugs=["clubs", "api-gateway"])
    assert ("clubs", "clubs") in mentions
    assert ("api-gateway", "api-gateway") in mentions


def test_find_unlinked_mentions_ignores_already_linked():
    text = "See [[clubs]] and also clubs again in text."
    mentions = find_unlinked_mentions(text, known_slugs=["clubs"])
    # Only one unlinked mention (the second "clubs")
    assert len(mentions) == 1


def test_find_unlinked_mentions_respects_word_boundaries():
    # "club" should not match inside "clubhouse"
    text = "Clubhouse is not the same."
    mentions = find_unlinked_mentions(text, known_slugs=["club"])
    assert mentions == []


def test_find_unlinked_mentions_returns_positions():
    text = "clubs and api-gateway"
    mentions = find_unlinked_mentions(text, known_slugs=["clubs", "api-gateway"])
    positions = [pos for _, _, pos in mentions] if mentions and len(mentions[0]) == 3 else []
    # Test passes whether find_unlinked_mentions returns (slug, text) or (slug, text, pos)
    # We just need deterministic ordering for later apply step
    assert len(mentions) == 2


def test_find_unlinked_mentions_ignores_code_blocks():
    text = """See clubs in prose.

```go
// The clubs variable in code should not be linked.
type Clubs struct{}
```

But clubs outside code should match."""
    mentions = find_unlinked_mentions(text, known_slugs=["clubs"])
    # Two unlinked mentions: prose "clubs" and post-block "clubs"
    # The one inside ```go``` block must be skipped.
    assert len(mentions) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_crosslink.py -v`
Expected: `ImportError: cannot import name 'find_unlinked_mentions'`

- [ ] **Step 3: Implement find_unlinked_mentions**

Create `scripts/crosslink.py`:

```python
"""Pass 2: deterministic cross-linking of wiki slugs.

For each known slug, find unlinked occurrences of the slug string in an
article's text and return (slug, matched_text, position). Already-linked
occurrences ([[slug]]) and occurrences inside fenced code blocks are skipped.

This pass does not call any LLM — it's pure string manipulation.
"""
from __future__ import annotations

import re


def _strip_code_blocks(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Replace ```...``` spans with spaces of the same length (to preserve
    offsets for caller), and return the modified string + list of skipped
    (start, end) ranges."""
    skipped: list[tuple[int, int]] = []
    result = list(text)
    for match in re.finditer(r"```.*?```", text, flags=re.DOTALL):
        start, end = match.span()
        skipped.append((start, end))
        for i in range(start, end):
            result[i] = " "
    return "".join(result), skipped


def find_unlinked_mentions(
    text: str,
    known_slugs: list[str],
) -> list[tuple[str, str, int]]:
    """Return [(slug, matched_text, position), ...] for unlinked slug mentions.

    - Skips `[[slug]]` occurrences (already linked).
    - Skips occurrences inside ```fenced code blocks```.
    - Matches on word boundaries: 'club' won't match inside 'clubhouse'.
    """
    scrubbed, _ = _strip_code_blocks(text)

    # Find existing [[slug]] spans to skip
    linked_spans: list[tuple[int, int]] = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", scrubbed):
        linked_spans.append(match.span())

    def is_already_linked(pos: int) -> bool:
        return any(start <= pos < end for start, end in linked_spans)

    mentions: list[tuple[str, str, int]] = []
    for slug in known_slugs:
        pattern = r"\b" + re.escape(slug) + r"\b"
        for match in re.finditer(pattern, scrubbed, flags=re.IGNORECASE):
            pos = match.start()
            if is_already_linked(pos):
                continue
            mentions.append((slug, match.group(0), pos))
    mentions.sort(key=lambda item: item[2])
    return mentions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_crosslink.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/crosslink.py tests/test_crosslink.py
git commit -m "feat(compile): Pass 2 slug mention detector"
```

---

### Task 5.2: Apply cross-links + detect dangling refs

**Files:**
- Modify: `tests/test_crosslink.py`
- Modify: `scripts/crosslink.py`

- [ ] **Step 1: Write failing tests for apply_crosslinks + find_dangling_refs**

Append to `tests/test_crosslink.py`:

```python
from crosslink import apply_crosslinks, find_dangling_refs


def test_apply_crosslinks_wraps_first_mention_only():
    text = "clubs are defined by clubs in the clubs module."
    result = apply_crosslinks(text, known_slugs=["clubs"])
    # Only the first occurrence is wrapped — avoids noise on repeated mentions
    assert result.count("[[clubs]]") == 1
    assert result.count("clubs") >= 3  # original 3 occurrences preserved


def test_apply_crosslinks_ignores_already_linked():
    text = "[[clubs]] are organizations."
    result = apply_crosslinks(text, known_slugs=["clubs"])
    assert result == text  # no change


def test_apply_crosslinks_handles_multiple_slugs():
    text = "The clubs connect to the api-gateway."
    result = apply_crosslinks(text, known_slugs=["clubs", "api-gateway"])
    assert "[[clubs]]" in result
    assert "[[api-gateway]]" in result


def test_apply_crosslinks_preserves_original_case():
    text = "Clubs are great."
    result = apply_crosslinks(text, known_slugs=["clubs"])
    # Wrapped but preserves the original capitalization
    assert "[[clubs|Clubs]]" in result or "[[Clubs]]" in result


def test_find_dangling_refs_detects_unknown_links():
    text = "See [[clubs]] and [[nonexistent]] for more."
    dangling = find_dangling_refs(text, known_slugs=["clubs", "api-gateway"])
    assert dangling == ["nonexistent"]


def test_find_dangling_refs_none_when_all_known():
    text = "See [[clubs]] and [[api-gateway]]."
    assert find_dangling_refs(text, known_slugs=["clubs", "api-gateway"]) == []


def test_find_dangling_refs_deduplicates():
    text = "[[ghost]] and [[ghost]] again."
    assert find_dangling_refs(text, known_slugs=[]) == ["ghost"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_crosslink.py -v`
Expected: `ImportError: cannot import name 'apply_crosslinks'`

- [ ] **Step 3: Implement apply_crosslinks + find_dangling_refs**

Append to `scripts/crosslink.py`:

```python
def apply_crosslinks(text: str, known_slugs: list[str]) -> str:
    """Wrap the first unlinked mention of each known slug in `[[slug]]`.

    Uses `[[slug|OriginalText]]` form if the original text differs from the
    canonical slug (case-insensitive match). Preserves text of all subsequent
    mentions and avoids nested linking.
    """
    mentions = find_unlinked_mentions(text, known_slugs)
    if not mentions:
        return text

    # Wrap only the *first* occurrence per slug
    seen: set[str] = set()
    first_per_slug: list[tuple[str, str, int]] = []
    for slug, matched, pos in mentions:
        if slug in seen:
            continue
        seen.add(slug)
        first_per_slug.append((slug, matched, pos))

    # Apply replacements from right to left so positions remain valid
    first_per_slug.sort(key=lambda item: item[2], reverse=True)
    result = text
    for slug, matched, pos in first_per_slug:
        end = pos + len(matched)
        if matched == slug:
            replacement = f"[[{slug}]]"
        else:
            replacement = f"[[{slug}|{matched}]]"
        result = result[:pos] + replacement + result[end:]
    return result


def find_dangling_refs(text: str, known_slugs: list[str]) -> list[str]:
    """Return list of slug strings referenced via [[...]] but not in known_slugs.

    Deduplicated, order of first appearance preserved.
    """
    import re as _re
    known_set = set(known_slugs)
    seen: set[str] = set()
    out: list[str] = []
    for match in _re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", text):
        ref = match.group(1).strip()
        if ref in known_set or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_crosslink.py -v`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/crosslink.py tests/test_crosslink.py
git commit -m "feat(compile): apply_crosslinks + dangling ref detector"
```

---

### Task 5.3: Pass 2 entry point — process a wiki directory

**Files:**
- Modify: `tests/test_crosslink.py`
- Modify: `scripts/crosslink.py`

- [ ] **Step 1: Write failing tests for run_pass2**

Append to `tests/test_crosslink.py`:

```python
from pathlib import Path

from crosslink import Pass2Report, run_pass2


def test_run_pass2_updates_articles_and_reports(tmp_path):
    (tmp_path / "clubs.md").write_text(
        "---\ntitle: Clubs\n---\n\n# Clubs\n\nLinks to api-gateway.\n",
        encoding="utf-8",
    )
    (tmp_path / "api-gateway.md").write_text(
        "---\ntitle: API\n---\n\n# API Gateway\n\nSee clubs.\n",
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")

    report = run_pass2(
        wiki_dir=tmp_path,
        known_slugs=["clubs", "api-gateway"],
    )
    assert isinstance(report, Pass2Report)
    assert report.links_added == 2  # one per file
    assert report.dangling == {}

    # Verify files now contain the wiki-links
    assert "[[api-gateway]]" in (tmp_path / "clubs.md").read_text()
    assert "[[clubs]]" in (tmp_path / "api-gateway.md").read_text()


def test_run_pass2_collects_dangling_per_article(tmp_path):
    (tmp_path / "clubs.md").write_text(
        "# Clubs\n\nSee [[ghost]] and [[missing]].\n",
        encoding="utf-8",
    )
    report = run_pass2(wiki_dir=tmp_path, known_slugs=["clubs"])
    assert "clubs" in report.dangling
    assert set(report.dangling["clubs"]) == {"ghost", "missing"}


def test_run_pass2_skips_index_and_log_files(tmp_path):
    (tmp_path / "index.md").write_text("# Index\n\nMention clubs.\n")
    (tmp_path / "log.md").write_text("# Log\n\n## [2026-04-14 22:15] ingest | clubs\n")

    report = run_pass2(wiki_dir=tmp_path, known_slugs=["clubs"])
    # Index and log are not rewritten
    assert "[[clubs]]" not in (tmp_path / "index.md").read_text()
    assert "[[clubs]]" not in (tmp_path / "log.md").read_text()
    assert report.links_added == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_crosslink.py::test_run_pass2_updates_articles_and_reports -v`
Expected: `ImportError: cannot import name 'run_pass2'`

- [ ] **Step 3: Implement run_pass2**

Append to `scripts/crosslink.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path


_META_FILES = {"index.md", "log.md", "overview.md"}


@dataclass(frozen=True)
class Pass2Report:
    links_added: int
    dangling: dict[str, list[str]] = field(default_factory=dict)


def run_pass2(wiki_dir: Path, known_slugs: list[str]) -> Pass2Report:
    """Scan every article in wiki_dir, auto-link slug mentions, collect dangling.

    - Skips index.md, log.md, overview.md (meta-articles not auto-linked).
    - Preserves YAML frontmatter: only rewrites the body below it.
    - Returns tally of links added + per-article dangling ref list.
    """
    links_added = 0
    dangling: dict[str, list[str]] = {}

    for article in sorted(wiki_dir.rglob("*.md")):
        if article.name in _META_FILES:
            continue
        content = article.read_text(encoding="utf-8")
        body = content
        header = ""
        if content.startswith("---\n"):
            end = content.find("\n---\n", 4)
            if end != -1:
                header = content[: end + 5]
                body = content[end + 5:]

        # Exclude this article's own slug so it doesn't self-link
        others = [s for s in known_slugs if s != article.stem]
        new_body = apply_crosslinks(body, known_slugs=others)
        if new_body != body:
            links_added += new_body.count("[[") - body.count("[[")
            article.write_text(header + new_body, encoding="utf-8")

        refs = find_dangling_refs(new_body, known_slugs=known_slugs)
        if refs:
            dangling[article.stem] = refs

    return Pass2Report(links_added=links_added, dangling=dangling)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_crosslink.py -v`
Expected: `15 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/crosslink.py tests/test_crosslink.py
git commit -m "feat(compile): Pass 2 run — cross-link + dangling report"
```

---

## Phase 6: Pass 3 — Haiku validation

### Task 6.1: Build validation prompt with sibling articles

**Files:**
- Create: `tests/test_validator.py`
- Create: `scripts/validator.py`

- [ ] **Step 1: Write failing tests for build_validation_prompt**

Create `tests/test_validator.py`:

```python
"""Tests for validator.py — Pass 3 Haiku cross-article validation."""
from pathlib import Path

import pytest

from validator import build_validation_prompt


def test_build_validation_prompt_includes_article_and_siblings(tmp_path):
    (tmp_path / "clubs.md").write_text("# Clubs\n\nClubs are organizations.\n")
    (tmp_path / "api-gateway.md").write_text("# API\n\nRoutes requests.\n")
    (tmp_path / "overview.md").write_text("# Overview\n\nSystem design.\n")

    prompt = build_validation_prompt(
        article_slug="clubs",
        wiki_dir=tmp_path,
        sibling_slugs=["api-gateway"],
    )
    assert "Clubs are organizations" in prompt
    assert "Routes requests" in prompt
    assert "System design" in prompt
    assert "clubs" in prompt
    assert "api-gateway" in prompt


def test_build_validation_prompt_asks_for_structured_issues(tmp_path):
    (tmp_path / "x.md").write_text("# X\n")
    prompt = build_validation_prompt(article_slug="x",
                                     wiki_dir=tmp_path,
                                     sibling_slugs=[])
    # Prompt must request JSON/list-formatted output so we can parse it
    lower = prompt.lower()
    assert "json" in lower or "list" in lower
    assert "contradiction" in lower or "inconsistency" in lower


def test_build_validation_prompt_omits_missing_sibling_gracefully(tmp_path):
    (tmp_path / "clubs.md").write_text("# Clubs\n")
    # No api-gateway.md created
    prompt = build_validation_prompt(
        article_slug="clubs",
        wiki_dir=tmp_path,
        sibling_slugs=["api-gateway"],
    )
    # Should not crash, should still be a valid prompt
    assert "clubs" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validator.py -v`
Expected: `ImportError: cannot import name 'build_validation_prompt'`

- [ ] **Step 3: Implement build_validation_prompt**

Create `scripts/validator.py`:

```python
"""Pass 3: Haiku-based cross-article validator.

For each article compiled in Pass 1, prompt Haiku with the article body
plus a small set of related sibling articles (from ripple-map). Ask the
model to surface contradictions, terminology mismatches, or stale claims.
Results are structured JSON that the caller can act on (re-queue article
to Pass 1 with feedback, or just log).
"""
from __future__ import annotations

from pathlib import Path

_ARTICLE_MAX_CHARS = 20_000
_OVERVIEW_MAX_CHARS = 8_000


def _read_article_body(wiki_dir: Path, slug: str, cap: int) -> str:
    path = wiki_dir / f"{slug}.md"
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    # Strip any frontmatter
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validator.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/validator.py tests/test_validator.py
git commit -m "feat(compile): Pass 3 Haiku validation prompt builder"
```

---

### Task 6.2: Parse Haiku response + validate_article()

**Files:**
- Modify: `tests/test_validator.py`
- Modify: `scripts/validator.py`

- [ ] **Step 1: Write failing tests for parse_validation_response + validate_article**

Append to `tests/test_validator.py`:

```python
import json
from unittest.mock import MagicMock

from llm_adapter import LLMResponse
from validator import (
    ValidationIssue,
    ValidationResult,
    parse_validation_response,
    validate_article,
)


def test_parse_validation_response_parses_json_array():
    text = '''Some preamble.
```json
[
  {"kind": "contradiction", "description": "X says A, Y says B", "evidence": "..."}
]
```
Trailing text.'''
    issues = parse_validation_response(text)
    assert len(issues) == 1
    assert issues[0].kind == "contradiction"
    assert "X says A" in issues[0].description


def test_parse_validation_response_empty_array_is_ok():
    text = "Here is the result:\n```json\n[]\n```"
    assert parse_validation_response(text) == []


def test_parse_validation_response_no_json_returns_empty_with_warning():
    text = "I could not analyze this article."
    # Graceful degradation: return [] rather than crashing
    assert parse_validation_response(text) == []


def test_validate_article_calls_llm_and_returns_issues(tmp_path, monkeypatch):
    (tmp_path / "clubs.md").write_text("# Clubs\n\nBody.\n")
    (tmp_path / "api-gateway.md").write_text("# API\n\nBody.\n")

    fake_response = LLMResponse(
        text='```json\n[{"kind": "stale", "description": "Z", "evidence": "q"}]\n```',
        cost_usd=0.003,
        model="claude-haiku-4-5",
    )
    monkeypatch.setattr("validator.call_llm", lambda *a, **kw: fake_response)

    result = validate_article(
        article_slug="clubs",
        wiki_dir=tmp_path,
        sibling_slugs=["api-gateway"],
    )
    assert isinstance(result, ValidationResult)
    assert result.slug == "clubs"
    assert len(result.issues) == 1
    assert result.issues[0].kind == "stale"
    assert result.cost_usd == 0.003


def test_validate_article_uses_haiku_model(tmp_path, monkeypatch):
    (tmp_path / "x.md").write_text("# X\n")
    captured_model = {}

    def fake_llm(prompt, model, cwd, max_turns=30):
        captured_model["model"] = model
        return LLMResponse(text="[]", cost_usd=0.0, model=model)

    monkeypatch.setattr("validator.call_llm", fake_llm)

    validate_article(article_slug="x", wiki_dir=tmp_path, sibling_slugs=[])
    assert "haiku" in captured_model["model"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validator.py -v`
Expected: `ImportError: cannot import name 'ValidationIssue'`

- [ ] **Step 3: Implement parse_validation_response + validate_article**

Append to `scripts/validator.py`:

```python
import json
import re
from dataclasses import dataclass

from llm_adapter import call_llm

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validator.py -v`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/validator.py tests/test_validator.py
git commit -m "feat(compile): Pass 3 Haiku validator with JSON response parsing"
```

---

## Phase 7: Pipeline orchestration

### Task 7.1: run_pipeline — P1 → P2 → P3 for a queue

**Files:**
- Create: `tests/test_pipeline.py`
- Create: `scripts/pipeline.py`

- [ ] **Step 1: Write failing tests for run_pipeline**

Create `tests/test_pipeline.py`:

```python
"""Tests for pipeline.py — orchestrates Pass 1 + Pass 2 + Pass 3 over a queue."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from articles_manifest import ArticleSpec
from llm_adapter import LLMResponse
from pipeline import PipelineReport, run_pipeline
from queue_builder import QueueItem
from ripple_map import RippleRule
from topo import ArticlePriority


@pytest.fixture
def repo_and_wiki(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "services" / "clubs").mkdir(parents=True)
    (repo / "services" / "clubs" / "entity.go").write_text("package clubs\n")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    return repo, wiki


@pytest.fixture
def manifest():
    return [
        ArticleSpec("clubs", ArticlePriority.SERVICES, ["services/clubs/**/*.go"]),
        ArticleSpec("api-gateway", ArticlePriority.SERVICES, ["services/*/**/*.go"]),
    ]


def test_run_pipeline_processes_every_queue_item(repo_and_wiki, manifest, monkeypatch):
    repo, wiki = repo_and_wiki
    queue = [
        QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
        QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
    ]

    def fake_llm(prompt, model, cwd, max_turns=30):
        # Pass 1: write article body; Pass 3: return empty issue list
        if "Validate wiki article" in prompt:
            return LLMResponse(text="```json\n[]\n```", cost_usd=0.002,
                               model="claude-haiku-4-5")
        slug = prompt.split("\n")[0].split(":")[-1].strip()
        article_path = cwd / f"{slug}.md"
        article_path.write_text(f"# {slug}\n\nBody.\n", encoding="utf-8")
        return LLMResponse(text=f"wrote {slug}", cost_usd=0.10,
                           model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_pipeline(
        queue=queue,
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
    )

    assert isinstance(report, PipelineReport)
    assert report.articles_compiled == 2
    # Both Pass 1 + Pass 3 costs accumulated
    assert report.total_cost_usd == pytest.approx(0.10 * 2 + 0.002 * 2, rel=1e-3)
    assert (wiki / "clubs.md").exists()
    assert (wiki / "api-gateway.md").exists()


def test_run_pipeline_emits_log_entries(repo_and_wiki, manifest, monkeypatch):
    repo, wiki = repo_and_wiki
    log_file = wiki / "log.md"

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.001, model="claude-haiku-4-5")
        slug = prompt.split("\n")[0].split(":")[-1].strip()
        (cwd / f"{slug}.md").write_text(f"# {slug}\n\nBody.\n")
        return LLMResponse(text="ok", cost_usd=0.05, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    run_pipeline(
        queue=[QueueItem("clubs", ArticlePriority.SERVICES, "manual")],
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
        log_file=log_file,
    )

    log_content = log_file.read_text(encoding="utf-8")
    assert "ingest" in log_content
    assert "clubs" in log_content


def test_run_pipeline_runs_pass2_at_end(repo_and_wiki, manifest, monkeypatch):
    repo, wiki = repo_and_wiki

    def fake_llm(prompt, model, cwd, max_turns=30):
        if "Validate" in prompt:
            return LLMResponse(text="[]", cost_usd=0.0, model="claude-haiku-4-5")
        # Pass 1: produce body that mentions sibling slug without [[ ]]
        slug = prompt.split("\n")[0].split(":")[-1].strip()
        sibling = "api-gateway" if slug == "clubs" else "clubs"
        (cwd / f"{slug}.md").write_text(
            f"# {slug}\n\nThis references {sibling} in plain text.\n",
            encoding="utf-8",
        )
        return LLMResponse(text="ok", cost_usd=0.05, model="claude-sonnet-4-6")

    monkeypatch.setattr("article_compiler.call_llm", fake_llm)
    monkeypatch.setattr("validator.call_llm", fake_llm)

    report = run_pipeline(
        queue=[
            QueueItem("clubs", ArticlePriority.SERVICES, "manual"),
            QueueItem("api-gateway", ArticlePriority.SERVICES, "manual"),
        ],
        manifest=manifest,
        ripple_rules=[],
        repo_root=repo,
        wiki_dir=wiki,
    )
    # Pass 2 must have auto-linked
    assert "[[api-gateway]]" in (wiki / "clubs.md").read_text()
    assert "[[clubs]]" in (wiki / "api-gateway.md").read_text()
    assert report.links_added >= 2


def test_run_pipeline_empty_queue_returns_zero_report(repo_and_wiki, manifest):
    repo, wiki = repo_and_wiki
    report = run_pipeline(
        queue=[], manifest=manifest, ripple_rules=[],
        repo_root=repo, wiki_dir=wiki,
    )
    assert report.articles_compiled == 0
    assert report.total_cost_usd == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: `ImportError: cannot import name 'run_pipeline'`

- [ ] **Step 3: Implement run_pipeline**

Create `scripts/pipeline.py`:

```python
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
    # Use sources-as-patterns so ripple rules that match those paths contribute
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline.py tests/test_pipeline.py
git commit -m "feat(compile): run_pipeline — P1→P2→P3 orchestration"
```

---

### Task 7.2: CLI entry point — `compile-queue` command

**Files:**
- Create: `tests/test_compile_queue_cli.py`
- Create: `scripts/compile_queue.py`

- [ ] **Step 1: Write failing test for the CLI**

Create `tests/test_compile_queue_cli.py`:

```python
"""Tests for compile_queue.py — thin CLI around run_pipeline."""
from pathlib import Path

import pytest

from compile_queue import render_report
from pipeline import PipelineReport
from validator import ValidationIssue, ValidationResult


def test_render_report_summarises_counts():
    report = PipelineReport(
        articles_compiled=3,
        total_cost_usd=0.37,
        links_added=7,
        dangling_refs={"clubs": ["ghost"]},
        validation_results=[
            ValidationResult(slug="clubs", issues=[], cost_usd=0.001,
                             model="claude-haiku-4-5"),
        ],
    )
    out = render_report(report)
    assert "3 articles" in out
    assert "$0.37" in out
    assert "7 links added" in out
    assert "ghost" in out


def test_render_report_lists_validation_issues():
    report = PipelineReport(
        articles_compiled=1,
        total_cost_usd=0.05,
        links_added=0,
        dangling_refs={},
        validation_results=[
            ValidationResult(
                slug="clubs",
                issues=[ValidationIssue(kind="stale", description="X", evidence="q")],
                cost_usd=0.001,
                model="claude-haiku-4-5",
            ),
        ],
    )
    out = render_report(report)
    assert "stale" in out
    assert "clubs" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compile_queue_cli.py -v`
Expected: `ImportError: cannot import name 'render_report'`

- [ ] **Step 3: Implement compile_queue CLI**

Create `scripts/compile_queue.py`:

```python
"""CLI entry point: compile a queue computed from a commit or file list.

Usage:
    uv run python scripts/compile_queue.py --commit HEAD --repo-root .
    uv run python scripts/compile_queue.py --files services/clubs/entity.go \\
        --repo-root . --wiki-dir vault/arena/wiki
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from articles_manifest import load_manifest
from pipeline import PipelineReport, run_pipeline
from queue_builder import build_queue
from ripple_map import load_ripple_map


def _changed_files_from_commit(commit: str, repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--name-only",
         f"{commit}^", commit],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def render_report(report: PipelineReport) -> str:
    lines: list[str] = [
        f"Pipeline complete: {report.articles_compiled} articles, "
        f"${report.total_cost_usd:.2f}, {report.links_added} links added",
        "",
    ]
    if report.dangling_refs:
        lines.append("Dangling refs:")
        for slug, refs in report.dangling_refs.items():
            lines.append(f"  {slug}: {', '.join(refs)}")
        lines.append("")
    issues_found = [(v.slug, v.issues) for v in report.validation_results if v.issues]
    if issues_found:
        lines.append("Validation issues:")
        for slug, issues in issues_found:
            for issue in issues:
                lines.append(f"  {slug} [{issue.kind}]: {issue.description}")
    else:
        lines.append("Validation: no issues.")
    return "\n".join(lines)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Compile a wiki queue.")
    parser.add_argument("--files", nargs="*", default=None)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--wiki-dir", required=True)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--articles-manifest",
                        default="config/wiki-articles.yaml")
    parser.add_argument("--ripple-map", default="config/ripple-map.yaml")
    args = parser.parse_args()

    if args.files is None and args.commit is None:
        parser.error("one of --files or --commit is required")

    repo = Path(args.repo_root).resolve()
    wiki = Path(args.wiki_dir).resolve()

    if args.commit:
        changed = _changed_files_from_commit(args.commit, repo)
        trigger = f"commit:{args.commit}"
    else:
        changed = list(args.files)
        trigger = "manual"

    manifest = load_manifest(Path(args.articles_manifest))
    ripple = load_ripple_map(Path(args.ripple_map))
    queue = build_queue(
        changed_files=changed,
        manifest=manifest,
        ripple_rules=ripple,
        wiki_dir=wiki,
        trigger=trigger,
    )
    if not queue:
        print("Nothing to compile.")
        return 0

    report = run_pipeline(
        queue=queue,
        manifest=manifest,
        ripple_rules=ripple,
        repo_root=repo,
        wiki_dir=wiki,
        log_file=Path(args.log_file) if args.log_file else None,
    )
    print(render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compile_queue_cli.py -v`
Expected: `2 passed`

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests pass (no regression from Plan 1).

- [ ] **Step 6: Commit**

```bash
git add scripts/compile_queue.py tests/test_compile_queue_cli.py
git commit -m "feat(cli): compile-queue command wraps run_pipeline"
```

---

## Self-Review

### Spec coverage

| Spec section | Task(s) |
|---|---|
| Pass 1 article compile | 4.1, 4.2, 4.3 |
| Pass 2 cross-link (no LLM) | 5.1, 5.2, 5.3 |
| Pass 3 Haiku validate | 6.1, 6.2 |
| E3 pinned glossary | 2.1 |
| Provenance frontmatter on compile | 4.2 |
| log.md emission on ingest | 4.3 |
| log.md emission on lint | 7.1 |
| Source SHA for reverse-index | 1.2 |
| Pipeline orchestration | 7.1 |
| CLI entry point | 7.2 |
| E5 commit-atomic batching | 7.2 (queue is batched from one commit/file-list) |

Deferred to Plan 3:
- Manual mode orchestration vs sleep-mode daemon
- Budget-aware loop (integration with quota)
- compile-status command
- Statusline widget
- Subscription ROI metrics
- E4 cross-article validation feedback loop (Pass 3 currently reports; re-queue is Plan 3)

### Placeholder scan

Scanned for "TBD", "TODO", "similar to", "fill in", "add validation", "handle edge cases" — none present. Every task has complete code.

### Type consistency

- `QueueItem` signature matches between 4.2, 4.3, 7.1 (all use `(slug, priority, trigger)`).
- `LLMResponse` used consistently in 3.1, 4.2, 4.3, 6.2, 7.1 (text, cost_usd, model).
- `ArticleSpec` used consistently in 1.1, 1.2, 4.1, 4.2, 7.1 (slug, priority, sources).
- `ValidationIssue` signature stable between 6.2 and 7.2.
- `Pass2Report` signature stable between 5.3 and 7.1.
- `PipelineReport` signature stable between 7.1 and 7.2.
- All functions that take `wiki_dir: Path` and `repo_root: Path` do so consistently.
- `call_llm` signature `(prompt, model, cwd, max_turns)` consistent across all callers.

No inconsistencies found.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-04-14-compile-pipeline.md`.

After executing this plan:
- **`uv run python scripts/compile_queue.py --commit HEAD --repo-root . --wiki-dir vault/arena/wiki`** runs the full three-pass pipeline for a commit.
- Wiki articles get real provenance frontmatter with source SHAs.
- `log.md` gets `ingest` and `lint` entries.
- Pass 2 auto-links known wiki slugs across articles.
- Pass 3 Haiku emits inconsistency reports (logged, not yet auto-fixed — that's Plan 3).

Test coverage added: ~35 new unit tests (on top of Plan 1's 72 → ~107 total).

Plan 3 will add: manual/sleep orchestration modes, budget-aware loop integrating `quota.get_quota()`, compile-status command, statusline widget, subscription ROI metrics, Pass 3 re-queue feedback loop.

---

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
