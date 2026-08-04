#!/usr/bin/env bash
# PostToolUse: guard the fragile "docs/-root JSON must be excluded from the
# publish rsync --delete" invariant (AGENTS.md; a real 2026-06-10 data-loss
# incident). run-related-tests.sh only runs name-matching tests, so adding a
# new docs export in src/dashboard.py (or writing a docs/*.json directly) can
# slip past — tests/test_publish_workflow.py would otherwise run nowhere.
#
# This hook is a THIN WRAPPER: it re-runs that canonical test at the moments
# the invariant can break, instead of re-implementing its allowlist logic
# (which would drift). Fails closed via exit 2 so the exclude list gets fixed.
set -uo pipefail

command -v uv >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')
[ -n "$path" ] || exit 0

proj="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$proj" || exit 0

# Trigger only when the change could affect the docs-publish invariant:
#   (a) a docs/-root JSON file is written/edited, or
#   (b) a pipeline source under src/ or scripts/ that emits a docs/*.json path,
#       or the publish workflow / the guard test itself is edited.
relevant=0
case "$path" in
  */docs/*.json)
    # Only docs/-root files matter; subdirs (tickers/, curation/) are handled
    # by wholesale --exclude 'curation' / the build and are not rsync-deleted.
    rel=${path##*/docs/}
    case "$rel" in */*) ;; *) relevant=1 ;; esac
    ;;
  *daily-publish-dashboard.yml|*tests/test_publish_workflow.py)
    relevant=1
    ;;
  */src/*.py|*/scripts/*.py)
    grep -qE "docs/[a-z0-9_]+\.json|DOCS_DIR" "$path" 2>/dev/null && relevant=1
    ;;
esac
[ "$relevant" -eq 1 ] || exit 0

if ! report=$(uv run python tests/test_publish_workflow.py 2>&1); then
  {
    echo "publish 除外リストの不変条件が壊れています。"
    echo "docs/ 直下の新しい JSON 出力は daily-publish-dashboard.yml の --exclude と"
    echo "tests/test_publish_workflow.py の EXPECTED_PRESERVED の両方に追加してください"
    echo "（追加漏れは次回 publish の rsync --delete で消えます / 2026-06-10 の事故）。"
    echo ""
    echo "$report"
  } >&2
  exit 2
fi
exit 0
