# Installed skills

Managed by [`npx skills`](https://github.com/vercel-labs/skills). Sources and
pinned hashes live in `../../skills-lock.json`. Do not hand-edit the vendored
skill directories — `npx skills update` overwrites them.

## Sources

| Source | Skills |
| --- | --- |
| `vercel-labs/skills` | `find-skills` |
| `obra/superpowers` | all 14 |
| `thedotmack/claude-mem` | 10 standalone skills only |
| `rebelytics/one-skill-to-rule-them-all` | `task-observer` (CC BY 4.0) |

The six memory-backed `claude-mem` skills (`mem-search`, `timeline-report`,
`weekly-digests`, `knowledge-agent`, `cloud-sync`, `how-it-works`) are
intentionally **not** installed: they require the claude-mem SQLite timeline
and worker service, which is a machine-local install (`npx claude-mem install`),
not something this repo can carry.

## Deliberate removals

`make-plan` and `do` (from `claude-mem`) were removed as duplicates of the
superpowers planning chain, which is deeper and integrates with the rest of
what is installed here.

Three installed skills still reference the removed slash commands in their
handoff sections. Substitute as follows:

| Referenced | Use instead |
| --- | --- |
| `/make-plan` | `superpowers:writing-plans` |
| `/do` | `superpowers:subagent-driven-development`, or `superpowers:executing-plans` when subagents are not in play |

Affected: `pathfinder` (handoff prompts), `design-is` (Phase 4 handoff),
`standup` (consolidation plan execution). The handoff in each case is a
copy-pasteable prompt, so the substitution is a routing change only — nothing
in those skills breaks.

## Trimmed vendored files

`task-observer` ships its upstream packaging alongside the skill. Removed on
install: `.github/workflows/` (the upstream repo's own release/publish CI,
referencing secrets this repo does not have), `.tessl-plugin/`, and two PNG
logos totalling 3.1 MB. The skill's own bundle manifest defines the skill as
`SKILL.md` plus the three `references/*.md` files, all of which are intact;
`skills-lock.json` hashes `SKILL.md` only, so the pin is unaffected.

Re-running `npx skills update task-observer` restores the removed files.

## Not configured

`task-observer` asks to be activated at the start of every session via a
CLAUDE.md instruction or a SessionStart hook. That is deliberately **not** set
up — it is a harness-level behaviour change and needs an explicit decision.
Note that `using-superpowers` makes a similar always-first claim, so enabling
both unconditionally would put two skills in contention for the same slot.

Its observation log lives under the stable project path
(`~/.claude/projects/<project-id>/skill-observations/`), not in this repo — so
on ephemeral remote checkouts the log does not survive the session.
