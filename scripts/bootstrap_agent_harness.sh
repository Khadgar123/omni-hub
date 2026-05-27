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

git submodule sync --recursive
git submodule update --init --recursive \
  agent-harness/swe-agent \
  agent-harness/promptfoo \
  agent-harness/argilla \
  agent-harness/graphiti

ensure_remote "agent-harness/swe-agent" "upstream" "https://github.com/SWE-agent/SWE-agent.git"
ensure_remote "agent-harness/promptfoo" "upstream" "https://github.com/promptfoo/promptfoo.git"
ensure_remote "agent-harness/argilla" "upstream" "https://github.com/argilla-io/argilla.git"
ensure_remote "agent-harness/graphiti" "upstream" "https://github.com/getzep/graphiti.git"

git submodule status --recursive \
  agent-harness/swe-agent \
  agent-harness/promptfoo \
  agent-harness/argilla \
  agent-harness/graphiti

echo "Agent harness forks are ready."
