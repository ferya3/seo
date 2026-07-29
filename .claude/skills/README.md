# Installed skills

Managed by [`npx skills`](https://github.com/vercel-labs/skills). Sources and
pinned hashes live in `../../skills-lock.json`. Do not hand-edit the vendored
skill directories — `npx skills update` overwrites them.

21 skills, curated for this repo: a Python SEO engine (`services/`, `agents/`)
with a web dashboard (`apps/web-dashboard`), mid-migration to a multi-service
architecture.

## Sources

| Source | Kept |
| --- | --- |
| `obra/superpowers` | 13 of 14 |
| `thedotmack/claude-mem` | 4 of 18 |
| `pbakaus/impeccable` | `impeccable` |
| `rebelytics/one-skill-to-rule-them-all` | `task-observer` (CC BY 4.0) |
| `vercel-labs/skills` | `find-skills` |

## What is here

**Development chain** (superpowers, mutually cross-referencing):
`brainstorming` → `writing-plans` → `subagent-driven-development` /
`executing-plans` → `requesting-code-review` / `receiving-code-review` →
`verification-before-completion` → `finishing-a-development-branch`. Plus
`test-driven-development`, `systematic-debugging`, `using-git-worktrees`,
`dispatching-parallel-agents`, and `using-superpowers` as the entry point.

**Codebase work:** `smart-explore` (tree-sitter AST search — the useful one on
158 Python files), `learn-codebase`, `pathfinder` (architecture audit, relevant
to the in-progress service split).

**Frontend:** `impeccable` — design, UX, accessibility, and anti-pattern
detection for the dashboard.

**Other:** `babysit` (PR watch), `find-skills`, `writing-skills`,
`task-observer`.

## Removed as not useful here

| Skill | Why |
| --- | --- |
| `make-plan`, `do` | Duplicated the deeper superpowers planning chain |
| `version-bump` | Releases Claude Code plugins (marketplace.json, npm) — this is a Python app |
| `oh-my-issues` | Clusters a backlog of dozens of issues; this repo has zero |
| `wowerpoint` | Generates kawaii slide-deck PDFs |
| `design-is` | Rams-principles design audit, superseded by `impeccable` |
| `standup` | Multi-agent worktree group chat; defaults to a `~/.claude-mem/` path that is not installed |
| `what-the` | "Explain this in plain English" — no workflow beyond answering |

Also never installed: the six memory-backed `claude-mem` skills (`mem-search`,
`timeline-report`, `weekly-digests`, `knowledge-agent`, `cloud-sync`,
`how-it-works`). They need the claude-mem SQLite timeline and worker, which is
a machine-local install (`npx claude-mem install`), not something this repo can
carry.

`pathfinder` still references the removed `/make-plan` and `/do` in its handoff
section. Substitute `superpowers:writing-plans` and
`superpowers:subagent-driven-development`. The handoff is a copy-pasteable
prompt, so this is a routing change only.

## Network behaviour worth knowing

`impeccable` pings `impeccable.style` for a concept-seed roll and an update
check. Set `IMPECCABLE_NO_TELEMETRY=1` (or the standard `DO_NOT_TRACK=1`) to
disable it. Its optional `scripts/generate-image.mjs` calls
`api.openai.com/v1/images/generations` and needs your own key; nothing else in
the tree calls it.

No other installed skill makes outbound network calls. The executable scripts
across `subagent-driven-development`, `systematic-debugging`, and `standup`'s
former tree were checked: read-only git commands and local file work only.

## Trimmed vendored files

`task-observer` shipped its upstream packaging alongside the skill. Removed:
`.github/workflows/` (the source repo's own release/publish CI, referencing
secrets this repo does not have), `.tessl-plugin/`, and two PNG logos totalling
3.1 MB. Its bundle manifest defines the skill as `SKILL.md` plus three
`references/*.md` files, all intact; `skills-lock.json` hashes `SKILL.md` only,
so the pin is unaffected. `npx skills update task-observer` restores them.

## Not configured

`task-observer` asks to be activated at the start of every session via a
CLAUDE.md instruction or a SessionStart hook. That is deliberately **not** set
up — it is a harness-level behaviour change and needs an explicit decision.
`using-superpowers` makes a similar always-first claim, so enabling both
unconditionally would put two skills in contention for the same slot.

`task-observer`'s observation log lives under the stable project path
(`~/.claude/projects/<project-id>/skill-observations/`), not in this repo — on
ephemeral remote checkouts it does not survive the session.
