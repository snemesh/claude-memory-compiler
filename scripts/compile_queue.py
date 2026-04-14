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
