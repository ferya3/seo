#!/bin/bash
# Prepares a Claude Code on the web session: project dependencies so tests and
# linters run, and the skill set this project is worked on with.
#
# Local sessions are skipped - a developer's own machine has its own global
# skills and virtualenv, and this should not reach into either.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# --- Python: the test suite and the linter -------------------------------
# requirements-dev.txt is the union of every service's requirements plus the
# tools, so this one file is all pytest and ruff need. Installed with the same
# interpreter the tests run under, so `python3 -m pytest` resolves.
# --ignore-installed blinker: the base image installs blinker through Debian's
# package manager, which leaves no RECORD file, so pip cannot uninstall it to
# satisfy Flask's dependency and aborts the whole install. Shadowing it in
# site-packages is fine and is what a virtualenv would do anyway.
echo "Installing Python dependencies..."
python3 -m pip install --quiet --disable-pip-version-check \
  --ignore-installed blinker -r requirements-dev.txt

# pytest.ini already sets pythonpath, but exporting it too means an ad-hoc
# `python3 -c "import shared..."` works without -m pytest.
echo 'export PYTHONPATH=".:services/engine"' >> "${CLAUDE_ENV_FILE:-/dev/null}"

# --- Skills ---------------------------------------------------------------
# Installed globally (-g), into ~/.claude/skills, rather than committed to the
# repo: the container is fresh each session, so this costs a download instead
# of eight megabytes of vendored files in git history.
echo "Installing skills..."

npx --yes skills@latest add obra/superpowers -g -a claude-code --copy -y \
  -s brainstorming -s writing-plans -s executing-plans \
  -s subagent-driven-development -s dispatching-parallel-agents \
  -s test-driven-development -s systematic-debugging \
  -s requesting-code-review -s receiving-code-review \
  -s verification-before-completion -s finishing-a-development-branch \
  -s using-git-worktrees -s using-superpowers -s writing-skills

npx --yes skills@latest add thedotmack/claude-mem -g -a claude-code --copy -y \
  -s smart-explore -s learn-codebase -s pathfinder -s babysit

npx --yes skills@latest add pbakaus/impeccable -g -a claude-code --copy -y -s impeccable
npx --yes skills@latest add vercel-labs/skills -g -a claude-code --copy -y -s find-skills
npx --yes skills@latest add rebelytics/one-skill-to-rule-them-all -g -a claude-code --copy -y \
  -s task-observer

# UI/UX Pro Max ships as its own CLI rather than a skills-repo, so it installs
# in two steps. -g here means the home directory, same destination as above.
npm install -g --silent ui-ux-pro-max-cli@latest
uipro init -g -a claude --force

echo "Session ready."
