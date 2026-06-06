#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <commit-message> <path> [path ...]" >&2
  exit 2
fi

message="$1"
shift

branch="${GITHUB_REF_NAME:-}"
if [ -z "$branch" ]; then
  branch="$(git branch --show-current)"
fi
if [ -z "$branch" ]; then
  branch="main"
fi

git config --local user.email "action@github.com"
git config --local user.name "GitHub Action"
git add -A "$@"

if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

git commit -m "$message"

# Re-establish push credentials. Steps such as anthropics/claude-code-action can
# replace the auth header that actions/checkout persisted, leaving an invalid
# credential by push time. When a token is provided, reset origin auth to it.
token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -n "${GITHUB_ACTIONS:-}" ] && [ -n "$token" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  git remote set-url origin "https://github.com/${GITHUB_REPOSITORY}.git"
  git config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true
  encoded="$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
  git config --local http.https://github.com/.extraheader "AUTHORIZATION: basic ${encoded}"
fi

for attempt in 1 2 3; do
  echo "Push attempt ${attempt} for ${branch}..."
  if git pull --rebase --autostash origin "$branch" && git push origin "HEAD:${branch}"; then
    exit 0
  fi

  git rebase --abort >/dev/null 2>&1 || true
  sleep $((attempt * 5))
done

echo "Failed to push after retries." >&2
exit 1
