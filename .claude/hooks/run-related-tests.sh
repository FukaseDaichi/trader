#!/usr/bin/env bash
# PostToolUse guard: after a Python file is edited, run the name-matching tests
# (tests/test_<stem>.py and tests/test_<stem>_*.py; an edited test runs itself).
# On failure it surfaces the output to Claude via exit 2 so the regression gets
# fixed immediately; it is silent on pass or when no matching test exists.
#
# This is the dev-time guard for the AGENTS.md invariant "the daily signal run
# must never break": the 23 tests under tests/ otherwise run nowhere automatically.
set -uo pipefail

# Project standard is uv; if it's missing, never block editing.
command -v uv >/dev/null 2>&1 || exit 0
command -v jq  >/dev/null 2>&1 || exit 0

path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')
[ -n "$path" ] || exit 0
case "$path" in *.py) ;; *) exit 0 ;; esac

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

base=$(basename "$path")
stem="${base%.py}"

# Resolve which test files to run (name convention from AGENTS.md).
tests=()
if [[ "$base" == test_*.py ]]; then
  # An edited test runs only itself.
  [ -f "tests/$base" ] && tests+=("tests/$base")
else
  for t in "tests/test_${stem}.py" tests/test_${stem}_*.py; do
    [ -f "$t" ] && tests+=("$t")
  done
fi

[ ${#tests[@]} -gt 0 ] || exit 0

failed=()
out=""
for t in "${tests[@]}"; do
  if ! result=$(uv run python "$t" 2>&1); then
    failed+=("$t")
    out+="----- $t -----"$'\n'"$result"$'\n'
  fi
done

if [ ${#failed[@]} -gt 0 ]; then
  {
    echo "関連テストが失敗しました: ${failed[*]}"
    echo "直前の編集がデイリー処理を壊した可能性があります。原因を直してください。"
    echo ""
    echo "$out"
  } >&2
  exit 2
fi
exit 0
