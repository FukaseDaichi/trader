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
