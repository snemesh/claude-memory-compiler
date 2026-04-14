"""Pass 2: deterministic cross-linking of wiki slugs.

For each known slug, find unlinked occurrences of the slug string in an
article's text and return (slug, matched_text, position). Already-linked
occurrences ([[slug]]) and occurrences inside fenced code blocks are skipped.

This pass does not call any LLM — it's pure string manipulation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


_META_FILES = {"index.md", "log.md", "overview.md"}


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


def apply_crosslinks(text: str, known_slugs: list[str]) -> str:
    """Wrap the first unlinked mention of each known slug in `[[slug]]`.

    Uses `[[slug|OriginalText]]` form if the original text differs from the
    canonical slug (case-insensitive match). Preserves text of all subsequent
    mentions and avoids nested linking.
    """
    mentions = find_unlinked_mentions(text, known_slugs)
    if not mentions:
        return text

    seen: set[str] = set()
    first_per_slug: list[tuple[str, str, int]] = []
    for slug, matched, pos in mentions:
        if slug in seen:
            continue
        seen.add(slug)
        first_per_slug.append((slug, matched, pos))

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
    known_set = set(known_slugs)
    seen: set[str] = set()
    out: list[str] = []
    for match in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", text):
        ref = match.group(1).strip()
        if ref in known_set or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


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

        others = [s for s in known_slugs if s != article.stem]
        new_body = apply_crosslinks(body, known_slugs=others)
        if new_body != body:
            links_added += new_body.count("[[") - body.count("[[")
            article.write_text(header + new_body, encoding="utf-8")

        refs = find_dangling_refs(new_body, known_slugs=known_slugs)
        if refs:
            dangling[article.stem] = refs

    return Pass2Report(links_added=links_added, dangling=dangling)
