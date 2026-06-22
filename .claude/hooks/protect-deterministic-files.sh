#!/usr/bin/env bash
# PreToolUse guard: tickers.yml / curation_pool.yml are owned by deterministic
# merge scripts (scripts/curation_merge.py / scripts/curation_pool_merge.py),
# never by direct agent edits. This blocks Edit/Write/MultiEdit on those files
# while leaving Bash (the scripts) and Read (the curation skills) untouched.
# See AGENTS.md "Key Conventions".
path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')
case "$(basename "$path")" in
  tickers.yml|curation_pool.yml)
    echo "BLOCKED: $(basename "$path") は直接編集禁止です。変更は scripts/curation_merge.py / scripts/curation_pool_merge.py 経由で行ってください（AGENTS.md 規約）。" >&2
    exit 2  # exit 2 = block the tool call and surface this reason to Claude
    ;;
esac
exit 0
