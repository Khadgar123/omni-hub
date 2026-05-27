#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ensure_clean() {
  local path="$1"
  if [ -n "$(git -C "$path" status --porcelain)" ]; then
    echo "Refusing to update $path because it has local changes." >&2
    exit 1
  fi
}

ensure_remote() {
  local path="$1"
  local name="$2"
  local url="$3"

  if git -C "$path" remote get-url "$name" >/dev/null 2>&1; then
    git -C "$path" remote set-url "$name" "$url"
  else
    git -C "$path" remote add "$name" "$url"
  fi
}

update_fork() {
  local path="$1"
  local branch="$2"

  ensure_clean "$path"
  git -C "$path" fetch origin --prune
  git -C "$path" fetch upstream --prune

  if git -C "$path" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$path" checkout "$branch"
  else
    git -C "$path" checkout -B "$branch" "origin/$branch"
  fi

  git -C "$path" merge --ff-only "upstream/$branch"
  git add "$path"
  echo "Updated $path to $(git -C "$path" rev-parse --short HEAD)"
}

git submodule update --init --recursive \
  agent-harness/swe-agent \
  agent-harness/promptfoo \
  agent-harness/argilla \
  agent-harness/graphiti

ensure_remote "agent-harness/swe-agent" "upstream" "https://github.com/SWE-agent/SWE-agent.git"
ensure_remote "agent-harness/promptfoo" "upstream" "https://github.com/promptfoo/promptfoo.git"
ensure_remote "agent-harness/argilla" "upstream" "https://github.com/argilla-io/argilla.git"
ensure_remote "agent-harness/graphiti" "upstream" "https://github.com/getzep/graphiti.git"

update_fork "agent-harness/swe-agent" "main"
update_fork "agent-harness/promptfoo" "main"
update_fork "agent-harness/argilla" "develop"
update_fork "agent-harness/graphiti" "main"

echo "Review changes, run tests, then commit the updated agent harness pointers."
