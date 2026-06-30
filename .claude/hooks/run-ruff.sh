#!/usr/bin/env bash
# PostToolUse: lint-autofix + format the just-edited Python file with ruff.
# Uses `uvx ruff` so ruff need NOT be a project dependency (no pyproject churn).
# Config (target version, rule set) is read from pyproject.toml [tool.ruff].
# Applies safe fixes in place; only remaining (unfixable) lint issues are
# surfaced to Claude via exit 2. Never blocks editing if uvx is unavailable.
set -uo pipefail

command -v uvx >/dev/null 2>&1 || exit 0
command -v jq  >/dev/null 2>&1 || exit 0

path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')
[ -n "$path" ] || exit 0
case "$path" in *.py) ;; *) exit 0 ;; esac
[ -f "$path" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

uvx ruff check --fix --quiet "$path" >/dev/null 2>&1 || true
uvx ruff format --quiet "$path" >/dev/null 2>&1 || true

# Report whatever ruff could not fix automatically.
if ! report=$(uvx ruff check "$path" 2>&1); then
  {
    echo "ruff の未修正の指摘があります ($path):"
    echo "$report"
  } >&2
  exit 2
fi
exit 0
