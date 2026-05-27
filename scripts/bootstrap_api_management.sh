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
git submodule update --init --recursive api-management/metapi api-management/ccLoad

ensure_remote "api-management/metapi" "upstream" "https://github.com/cita-777/metapi.git"
ensure_remote "api-management/ccLoad" "upstream" "https://github.com/caidaoli/ccLoad.git"

git submodule status --recursive api-management/metapi api-management/ccLoad

echo "API management forks are ready."
