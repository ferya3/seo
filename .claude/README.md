# Claude Code session setup

`hooks/session-start.sh` runs once at the start of every Claude Code on the web
session, registered through `settings.json`. It does two things:

**Python dependencies.** Installs `requirements-dev.txt` with the same
interpreter the tests run under. Without it a web session starts with no
pytest and no fastapi, so neither the test suite nor an import check works.

**Skills.** Installs the skill set this project is worked on with, globally
into `~/.claude/skills` rather than committed here - the container is fresh
each session, so this costs a ~35s download instead of 8 MB of vendored files
in git history. 28 skills: the superpowers development chain, code-exploration
skills, `impeccable` and the UI/UX Pro Max set for the dashboard, plus
`find-skills` and `task-observer`.

Local sessions exit immediately (`CLAUDE_CODE_REMOTE` guard) - a developer's
own machine has its own global skills and virtualenv, and this should not
reach into either.

## Changing the skill list

Edit the `npx skills add` lines in the hook. `npx skills find <query>` searches
for more; `npx skills remove -g -s <name>` drops one from the current session.

## Note

`impeccable` sends a telemetry ping to impeccable.style. Add
`export IMPECCABLE_NO_TELEMETRY=1` to the hook to disable it.
