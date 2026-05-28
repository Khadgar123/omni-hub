#!/usr/bin/env bash
# add_pending_harness_forks.sh — promote pending forks (DSPy / OpenHands / Opik) into real submodules.
#
# Usage:
#   1. Fork the upstream repo to your personal GitHub (Khadgar123) via the web UI:
#        - https://github.com/stanfordnlp/dspy        -> Khadgar123/dspy
#        - https://github.com/All-Hands-AI/OpenHands  -> Khadgar123/OpenHands
#        - https://github.com/comet-ml/opik           -> Khadgar123/opik
#   2. Run:   ./scripts/add_pending_harness_forks.sh dspy
#             ./scripts/add_pending_harness_forks.sh openhands
#             ./scripts/add_pending_harness_forks.sh opik
#      (or "all" to attempt all three)
#
# The script:
#   - reads agent-harness/manifest.json for the upstream URL and branch
#   - assumes your fork lives at https://github.com/Khadgar123/<repo>.git (override with --origin)
#   - runs git submodule add
#   - sets the "upstream" remote
#   - prints next commands

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ORIGIN_USER="${ORIGIN_USER:-Khadgar123}"

c_green() { printf "\033[1;32m%s\033[0m\n" "$*"; }
c_yellow(){ printf "\033[1;33m%s\033[0m\n" "$*"; }
c_red()   { printf "\033[1;31m%s\033[0m\n" "$*" >&2; }

add_one() {
  local id="$1"
  local entry path upstream branch
  entry="$(python3 -c "
import json, sys
m = json.load(open('agent-harness/manifest.json'))
for f in m.get('pending_forks', []):
    if f['id'] == sys.argv[1]:
        print(f\"{f['path']}\t{f['upstream']}\t{f['branch']}\")
        break
" "$id")"

  if [ -z "$entry" ]; then
    c_red "id '$id' not found in pending_forks. Available:"
    python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
for f in m.get('pending_forks', []):
    print('  ', f['id'])
"
    return 1
  fi

  IFS=$'\t' read -r path upstream branch <<<"$entry"

  if [ -d "$path/.git" ] || git config --file .gitmodules --get "submodule.${path}.url" >/dev/null 2>&1; then
    c_yellow "Already added: $path — skipping submodule-add."
    return 0
  fi

  # derive fork origin from upstream basename, e.g. https://github.com/Khadgar123/OpenHands.git
  local repo_basename
  repo_basename="$(basename "$upstream" .git)"
  local origin_url="https://github.com/${ORIGIN_USER}/${repo_basename}.git"

  c_green "Adding submodule: $path"
  c_green "  upstream : $upstream"
  c_green "  origin   : $origin_url   (override with ORIGIN_USER=... or git remote set-url)"
  c_green "  branch   : $branch"

  if ! git ls-remote "$origin_url" &>/dev/null; then
    c_red "ERR: Cannot reach $origin_url"
    c_red "     Make sure you've forked $upstream to your account first."
    return 1
  fi

  git submodule add -b "$branch" "$origin_url" "$path"
  git -C "$path" remote add upstream "$upstream" 2>/dev/null || \
    git -C "$path" remote set-url upstream "$upstream"
  git -C "$path" fetch upstream --prune

  c_green "Done. Next:"
  c_green "  git -C $path checkout $branch"
  c_green "  git -C $path merge --ff-only upstream/$branch"
  c_green "  git add .gitmodules $path"
  c_green "  git commit -m \"Add $id harness fork\""
}

main() {
  if [ $# -eq 0 ]; then
    cat <<USAGE
Usage: $0 <id> [<id>...]    (or 'all')

Available pending forks:
USAGE
    python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
for f in m.get('pending_forks', []):
    print(f\"  {f['id']:<10} -> {f['upstream']}\")
    print(f\"             {f['role']}\")
"
    exit 1
  fi

  if [ "$1" = "all" ]; then
    set -- $(python3 -c "
import json
m = json.load(open('agent-harness/manifest.json'))
print(' '.join(f['id'] for f in m.get('pending_forks', [])))
")
  fi

  for id in "$@"; do
    add_one "$id" || c_yellow "  (continuing with the next)"
    echo
  done
}

main "$@"
