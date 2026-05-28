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

manifest_entries() {
  python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
for fork in m.get('forks', []):
    print('\t'.join([
        fork['path'], fork['origin'], fork['upstream'], fork['branch'],
    ]))
"
}

mapfile -t HARNESS_PATHS < <(python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
for fork in m.get('forks', []):
    print(fork['path'])
")

git submodule update --init --recursive "${HARNESS_PATHS[@]}"

while IFS=$'\t' read -r path origin upstream branch; do
  ensure_remote "$path" "origin" "$origin"
  ensure_remote "$path" "upstream" "$upstream"
  update_fork "$path" "$branch"
done < <(manifest_entries)

echo "Review changes, run tests, then commit the updated agent harness pointers."
