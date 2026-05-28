#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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

manifest_paths() {
  python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
for fork in m.get('forks', []):
    print(fork['path'])
"
}

manifest_remotes() {
  python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
for fork in m.get('forks', []):
    print('\t'.join([fork['path'], fork['origin'], fork['upstream']]))
"
}

mapfile -t HARNESS_PATHS < <(manifest_paths)

git submodule sync --recursive
git submodule update --init --recursive "${HARNESS_PATHS[@]}"

while IFS=$'\t' read -r path origin upstream; do
  ensure_remote "$path" "origin" "$origin"
  ensure_remote "$path" "upstream" "$upstream"
done < <(manifest_remotes)

git submodule status --recursive "${HARNESS_PATHS[@]}"

echo "Agent harness forks are ready."
