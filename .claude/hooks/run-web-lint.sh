#!/usr/bin/env bash
# PostToolUse: lint-autofix + type-check the just-edited frontend file under web/.
# This is the TypeScript/React counterpart of run-ruff.sh — Python was already
# guarded (ruff + related tests) but web/ (.ts/.tsx) had no post-edit check.
#
# - eslint --fix applies safe fixes in place (flat config: web/eslint.config.mjs).
# - tsc --noEmit type-checks the whole web project (TS is project-wide, not
#   per-file), catching type errors the single-file edit may have introduced.
# Only remaining, unfixable issues are surfaced to Claude via exit 2.
# Never blocks editing when npx or web/node_modules are unavailable.
set -uo pipefail

command -v npx >/dev/null 2>&1 || exit 0
command -v jq  >/dev/null 2>&1 || exit 0

path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')
[ -n "$path" ] || exit 0

proj="${CLAUDE_PROJECT_DIR:-$(pwd)}"

# Only handle lintable frontend sources under web/.
case "$path" in
  "$proj"/web/*) ;;
  *) exit 0 ;;
esac
case "$path" in
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs) ;;
  *) exit 0 ;;
esac
[ -f "$path" ] || exit 0

# Fresh clone / deps not installed → don't block editing.
[ -d "$proj/web/node_modules" ] || exit 0

cd "$proj/web" || exit 0

# 1) Autofix the edited file in place, then collect any remaining lint issues.
npx --no-install eslint --fix "$path" >/dev/null 2>&1 || true
eslint_report=""
if ! eslint_report=$(npx --no-install eslint "$path" 2>&1); then
  : # non-zero → remaining issues captured in $eslint_report
else
  eslint_report=""
fi

# 2) Project-wide type check (TypeScript is not per-file).
tsc_report=""
if ! tsc_report=$(npx --no-install tsc --noEmit -p tsconfig.json 2>&1); then
  : # non-zero → type errors captured in $tsc_report
else
  tsc_report=""
fi

if [ -n "$eslint_report" ] || [ -n "$tsc_report" ]; then
  {
    echo "web/ のフロントエンドチェックで未修正の指摘があります ($path):"
    [ -n "$eslint_report" ] && { echo "----- eslint -----"; echo "$eslint_report"; }
    [ -n "$tsc_report" ]    && { echo "----- tsc --noEmit -----"; echo "$tsc_report"; }
  } >&2
  exit 2
fi
exit 0
