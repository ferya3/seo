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
