# Chunked Wiki Compile with Budget-Aware Sync

**Status:** Design approved, awaiting implementation plan
**Date:** 2026-04-14
**Author:** Sergii Nemesh + Claude Opus 4.6

## Problem

Current `compile.py` processes daily logs as monolithic units. Two risks:

1. **Timeout loss.** Large repos (~100+ files, ~50 wiki articles) exceed single-invocation budgets. The existing 1800s subprocess timeout in `ingest-commits.py` is a hard kill — work in progress is lost.
2. **No ripple handling.** A single fix touching 5-6 services silently desynchronizes the wiki because staleness detection is file-level, not dependency-aware.

## Goals

- **Resumability.** No single failure loses more than one article's worth of work.
- **Quality over cost.** Cross-service changes produce coherent wiki updates, not staggered drift.
- **Budget awareness.** System reads Max-plan subscription quota before every chunk, never exceeds user-defined thresholds.
- **Two modes.** Manual (interactive, user-driven) and sleep (user triggers, system runs until queue done or budget exhausted).
- **Observability.** Human-readable log of every operation, glanceable statusline, detailed status command.

## Non-goals

- Real-time file watchers. Sync is triggered by explicit user intent or commit events, not FS events.
- launchd/systemd scheduling. Budget-aware loop with checkpoint makes periodic ticks unnecessary.
- Replacing existing `daily/` session logs. Those track conversation state; this design tracks wiki operations.

## Architecture

### Three-pass compile per sync

**Pass 1 — Ingest (Sonnet 4.6).**
Chunk = one wiki article (not one file). Articles are the natural unit of meaning (~50 in current wiki index). Each article declares a set of source files via config pattern. One LLM call reads all sources, produces/updates the article.

Articles are processed in topological order (dependency-first):
1. **P1 — Models/entities** (`fan-entity`, `club-entity`, `campaign`, `ad-task`, `geo-tiers`)
2. **P2 — Services** (topologically sorted by proto-import graph)
3. **P3 — Features** (reference P1 + P2)
4. **P4 — Integrations, ADRs** (terminal leaves)
5. **P5 — Overview** (synthesizes everything above)

Rationale: when writing article N, all of N's dependencies are already in the wiki → correct `[[link]]` references without speculation.

**Pass 2 — Cross-link (no LLM).**
Script scans all articles produced in Pass 1. For each known wiki slug, finds unlinked textual mentions and converts to `[[slug]]`. Detects dangling `[[refs]]` → writes to lint report.

**Pass 3 — Validate (Haiku 4.5).**
For each article: short Haiku call checks against `overview.md` + glossary + related articles (via ripple-map). Outputs list of inconsistencies. If issues found, article returns to Pass 1 queue with validator feedback as additional context.

### Enhancements for cross-service ripple (E1-E5)

**E1. Dependency-aware staleness.**
Each article writes provenance frontmatter: list of source files with SHA. Compile builds reverse index `file → articles`. When a file changes, all articles listing it become candidates for recompile. Queue is re-sorted topologically before running.

Provenance format:
```yaml
---
sources:
  - path: services/clubs/internal/entity.go
    sha: abc123...
  - path: proto/clubs/v1/clubs.proto
    sha: def456...
compiled_at: 2026-04-14T22:15:00+02:00
model: sonnet-4-6
cost_usd: 0.12
triggered_by: manual | ripple:clubs | commit:cf6d534
---
```

**E2. Ripple map.**
`.memory/ripple-map.yaml` declares non-obvious dependencies not derivable from proto imports (shared DB tables, Kafka topics, cross-service events). Auto-seeded from proto-import graph + api-gateway route map; human-editable for hidden deps.

```yaml
patterns:
  - match: "proto/clubs/**"
    revalidate: [api-gateway, club-finance, referrals, club-self-registration]
  - match: "services/auth-service/internal/jwt/**"
    revalidate: [fan-registration, club-self-registration, wallet]
  - match: "services/referrals/internal/event/**"
    revalidate: [agents, club-finance, referral-system]
```

**E3. Pinned glossary.**
Every Pass 1 call receives in system prompt:
- Canonical terms from `[[confluence-glossary]]` article
- Key invariants from `overview.md` (bounded contexts, status machines)
- List of all known wiki slugs (for correct `[[link]]` usage)

Size: ~3KB. Prevents vocabulary drift across 50 article compiles.

**E4. Cross-article validation.**
Pass 3 Haiku checks each article against its ripple-map siblings for contradictions and terminology mismatches. Results feed back to Pass 1 if issues exceed threshold.

**E5. Commit-atomic batching.**
Sync is never triggered per-file. Single commit → compute full fan-out via E1 reverse index + E2 ripple map → queue all affected articles → process as one atomic session. User sees coherent wiki update, not staggered drift.

## Modes

### Manual mode (foreground, in session)

User: "проанализируй коммит X и обнови вики"

Flow in current interactive Claude session:
1. `git diff X^ X --name-only` → changed files
2. Compute fan-out: changed files → affected articles via E1 + E2
3. Sort topologically
4. Report: "Queue: 7 articles, est $0.65, ~4 minutes. Proceed?"
5. On approve: run Pass 1 → Pass 2 → Pass 3 in foreground, Claude reports progress conversationally
6. Each article commit goes to git immediately; each step appends to `log.md`
7. Final: "Done. 7 articles updated, 0 inconsistencies, total $0.72."

### Sleep mode (detached background process)

User: "иду спать, синкай до утра"

1. Claude computes full queue (pending articles + ripple fan-out from recent commits)
2. Reads `oauth/usage` → 5h and 7d quotas
3. Reports pre-flight plan:
   ```
   Queue: 47 articles, est cost $4.70, duration ~3h
   5h-window: 72% free → 43 articles fit this window
   4 articles roll to next window @ 08:15
   7d-quota: 18% used, plenty of room
   Proceed? [y/n]
   ```
4. On approve: launches detached Python process (`nohup python sync-loop.py &`)
5. Claude exits session safely; sync-loop runs independently
6. Main loop:
   ```
   while queue not empty:
     refresh oauth/usage
     if five_hour.used_pct > 85 or seven_day.used_pct > 90:
       sleep until resets_at + 30s
       continue
     chunk = queue.next()
     result = compile_article(chunk)
     commit_to_git(result)
     append_log_md(result)
     update_sync_state(result)
   ```
7. Next session: "как прошло?" → Claude reads `sync-state.json` + tail of `log.md` → reports

### Control commands (recognized in any session)

- **"статус"** / **"как синк"** → read state, render status
- **"стоп"** → write kill-flag to `sync-state.json`; loop honors it after current article (never mid-chunk)
- **"resume"** → restart daemon, continues from `status: interrupted` queue
- **"план синка на коммит X"** → dry-run: compute queue + cost, report, do NOT execute

## Budget awareness

### Quota source

**Primary:** `GET https://api.anthropic.com/api/oauth/usage` with OAuth token from Claude Code credentials.

Response shape:
```json
{
  "five_hour": { "used_percentage": 0-100, "resets_at": <unix_ts> },
  "seven_day": { "used_percentage": 0-100, "resets_at": <unix_ts> }
}
```

Only available for Pro/Max subscribers via OAuth (not API-key sessions). Populates after first API response in session.

**Fallback:** If endpoint unreachable, parse `~/.claude/projects/**/*.jsonl` for tokens-used in rolling 5h window, compute cost with model pricing table (see `ccusage` as reference).

**Hard per-chunk cap:** Each article compile uses `--max-budget-usd` flag (value = 2× avg observed cost, from `state.json`). Defense-in-depth against runaway chunks.

### Thresholds (configurable in `.memory/config.json`)

```json
{
  "budget": {
    "five_hour_stop_pct": 85,
    "seven_day_stop_pct": 90,
    "sleep_on_exhaust": true,
    "per_chunk_max_usd": 0.50,
    "subscription_tier": "max-20x",
    "subscription_usd_monthly": 200
  }
}
```

### Subscription ROI tracking

Monthly aggregate in `sync-state.json`:
- `subscription_paid_usd`: 200
- `api_equivalent_used_usd`: computed from jsonl × model pricing
- `roi_pct`: `api_equivalent / subscription_paid * 100`
- Surfaced in `compile-status` command and monthly summary

## Observability

### log.md — first-class artifact (llm-wiki pattern)

Location: `vault/arena/log.md` (actual wiki root — per existing project layout). Append-only. Parseable with standard unix tools (`grep "^## \[" log.md | tail -20`).

Entry format: `## [YYYY-MM-DD HH:MM] <type> | <summary> | <metadata>`

Types: `ingest`, `lint`, `query`, `sync-start`, `sync-complete`, `sync-resumed`, `sync-interrupted`, `window-exhausted`, `budget-warning`, `error`.

Example:
```markdown
## [2026-04-14 22:15] sync-start | 47 articles queued, est $4.70, budget 5h:72%
## [2026-04-14 22:18] ingest | clubs | cost $0.12 | sources: 8 files | commit a7f2c9
## [2026-04-14 22:21] ingest | api-gateway | cost $0.18 | sources: 14 files | commit b4e0d1
## [2026-04-14 22:24] ingest | club-finance | cost $0.10 | triggered_by: ripple:clubs
## [2026-04-14 23:45] lint | cross-article validation | 2 inconsistencies auto-fixed
## [2026-04-15 01:30] window-exhausted | 5h:92%, sleeping until 03:15
## [2026-04-15 03:16] sync-resumed | 31/47 done
## [2026-04-15 05:02] sync-complete | 47 articles | total $4.82 | duration 6h47m
```

Purpose: human-readable audit trail, git-diff-friendly, teaches future sessions without LLM processing.

### Operational state files (not committed to wiki git)

- **`.memory/sync-state.json`** — current queue + status. Atomically rewritten after each article.
- **`.memory/progress.jsonl`** — machine events for statusline/UI consumers. Append-only, ephemeral per sync.

### Statusline (ccstatusline-compatible, 3-4 lines)

```
🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜ 32/47 (68%) • compiling: club-finance • phase P2/services
⏱ elapsed 1h47m • ETA 2h12m (08:42) • 5h-reset in 2h03m • 7d-reset in 4d12h
💰 5h:68%▪ 7d:23%▪ • sync $3.12 (would cost $4.70 at API) • cache hit 78% • $0.10/art avg
✓ last commit a7f2c9 "wiki: compile clubs [31/47]" • 0 retries • 0 inconsistencies
```

Color-coded progress cells:
- 🟦 done
- 🟨 current
- ⬜ pending (safe within budget)
- 🟧 pending (tight — at current pace, will exhaust 5h window mid-queue)
- 🟥 pending (will be deferred to next window)

### `compile-status` command

Full dump:
- Phase breakdown (P1-P5: done/pending/cost each)
- Top-5 most expensive articles (budget optimization data)
- Pass 3 inconsistency report
- Recent errors/retries with causes
- Ripple trigger: which commit initiated sync, which files → which articles
- Projected finish time ± confidence
- Subscription ROI: month-to-date spend, equivalent-at-API cost

## File layout

```
~/claude-memory-compiler/
├── scripts/
│   ├── compile.py                  # existing, refactored to process per-article
│   ├── sync.py                     # NEW: manual-mode entrypoint
│   ├── sync-loop.py                # NEW: sleep-mode daemon
│   ├── compile-status.py           # NEW: status command
│   ├── quota.py                    # NEW: oauth/usage client + fallback
│   ├── queue.py                    # NEW: queue builder + topological sort
│   ├── ripple.py                   # NEW: E2 ripple-map evaluator
│   └── lint.py                     # existing, extended for Pass 3
├── config/
│   ├── wiki-articles.yaml          # NEW: article → sources mapping
│   └── ripple-map.yaml             # NEW: E2 manifest (auto-seeded, human-editable)
├── docs/
│   └── specs/
│       └── 2026-04-14-wiki-compile-chunked-design.md   # this file

/Volumes/snemesh/Python/Progects/Arena/arena/
├── .memory/
│   ├── sync-state.json             # current queue (ephemeral-ish)
│   ├── progress.jsonl              # machine events (per-sync)
│   └── config.json                 # user thresholds
└── vault/arena/
    ├── log.md                      # NEW: llm-wiki chronological log
    ├── index.md                    # existing
    ├── overview.md                 # existing
    └── <article-slug>.md           # existing, augmented with provenance frontmatter
```

## Testing strategy

- **Dry-run mode** (`sync --dry-run`) exercises queue-builder + ripple + cost-estimate without LLM calls. Verified by assertion on queue size + topological order.
- **Quota mock**: inject fixed `oauth/usage` response to test window-exhausted → sleep → resume transitions without waiting 5h.
- **Ripple correctness**: seed ripple-map, change one file in test fixture, assert all declared revalidate-targets enter queue.
- **Resume correctness**: kill sync-loop mid-article, restart, assert queue continues without duplicate compiles.
- **Cross-article validation**: seed two articles with deliberate contradiction, assert Pass 3 flags it.

## Open questions (deferred to implementation)

- Should `query` log entries also include pages_read with line-count, or just slug list?
- Should Pass 3 validation be mandatory or opt-in per-article (some articles are leaf facts, low value of cross-check)?
- Ripple-map bootstrapping: initial auto-seed scope (proto imports only? + api-gateway routes? + DB schema shared-table analysis?)
- Statusline integration: ship our own `ccstatusline`-compatible widget, or rely on existing community tools?

## Decisions log

- **Chunk = article, not file, not commit.** File is too granular (cache burn), commit cuts across services (coordination hell).
- **Topological, not priority-first.** Foundations-first ensures downstream articles link correctly.
- **No launchd.** Budget math + checkpoint makes ticks redundant; reboot-resilience traded for simplicity.
- **`oauth/usage` over jsonl-parsing.** Source of truth vs proxy.
- **`log.md` in wiki/, not .memory/.** It's a first-class wiki artifact per llm-wiki pattern — part of knowledge, not just operational exhaust.
- **Provenance in frontmatter, not sidecar file.** Co-located with content; survives rename/move trivially.

## Related

- [llm-wiki pattern](https://gist.github.com/snemesh/0f36e7e4dcb7e536b5238dbf3f9441b3) — inspiration for `log.md` as first-class artifact, ingest/query/lint taxonomy
- `ccusage` (ryoppippi/ccusage) — fallback quota-source reference implementation
- `claude-usage` (phuryn/claude-usage) — Pro/Max progress bar dashboard reference
