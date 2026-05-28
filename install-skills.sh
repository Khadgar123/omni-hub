#!/usr/bin/env bash
# install-skills.sh — 给 Claude Code 和 Codex CLI 同时安装一组高 star、被验证过的工程类 skill。
#
# 做了三件事：
#   1. 在 ~/.agents/skills/ 建立"单一真源"，并把 ~/.claude/skills、~/.codex/skills 软链过去
#   2. clone 三个核心 skill 仓库：
#        - obra/superpowers                       (150k★, 官方 marketplace)
#        - awesome-skills/code-review-skill       (17+ 语言代码评审)
#        - anthropics/claude-plugins-official     (内含官方 skill-creator)
#   3. 给当前仓库（omni-hub）也建一份项目级 .agents/skills/ 软链
#
# 设计原则：
#   - 完全幂等：重跑只更新，不重建已有内容
#   - DRY-RUN 模式：先看再做， DRY_RUN=1 ./install-skills.sh
#   - 任何破坏性操作都先备份原目录到 ~/.agents-skills-backup-<timestamp>/

set -euo pipefail

GLOBAL_ROOT="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"
CODEX_DIR="${HOME}/.codex/skills"
DRY_RUN="${DRY_RUN:-0}"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="${HOME}/.agents-skills-backup-${TS}"

c_blue()  { printf "\033[1;34m%s\033[0m\n" "$*"; }
c_green() { printf "\033[1;32m%s\033[0m\n" "$*"; }
c_yellow(){ printf "\033[1;33m%s\033[0m\n" "$*"; }
c_red()   { printf "\033[1;31m%s\033[0m\n" "$*"; }

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf "  \033[2m$ %s\033[0m\n" "$*"
  else
    eval "$@"
  fi
}

backup_if_real_dir() {
  local target="$1"
  if [[ -e "$target" && ! -L "$target" ]]; then
    mkdir -p "$BACKUP_ROOT"
    c_yellow "  备份原目录：$target -> $BACKUP_ROOT/$(basename "$target")"
    run "mv \"$target\" \"$BACKUP_ROOT/$(basename "$target")\""
  fi
}

ensure_symlink() {
  local link="$1" target="$2"
  if [[ -L "$link" ]]; then
    local current
    current="$(readlink "$link")"
    if [[ "$current" == "$target" ]]; then
      c_green "  软链已正确：$link -> $target"
      return
    fi
    c_yellow "  替换错指的软链：$link (旧 -> $current)"
    run "rm \"$link\""
  fi
  backup_if_real_dir "$link"
  run "mkdir -p \"$(dirname "$link")\""
  run "ln -s \"$target\" \"$link\""
  c_green "  建立软链：$link -> $target"
}

flatten_inner_skills() {
  # 多数 agent 只扫 ~/.agents/skills 的第一层。superpowers 把 skill 放在
  # <repo>/skills/<name>/SKILL.md，anthropic-official 放在
  # plugins/<plugin>/skills/<name>/SKILL.md。把它们软链到顶层。
  local root="$GLOBAL_ROOT"
  [[ -d "$root/superpowers/skills" ]] && {
    for d in "$root/superpowers/skills"/*/; do
      [[ -d "$d" ]] || continue
      local name; name="$(basename "$d")"
      if [[ -e "$root/$name" ]]; then
        c_green "  skip (existed): $name"
        continue
      fi
      run "ln -s \"superpowers/skills/$name\" \"$root/$name\""
      c_green "  flatten: $name -> superpowers/skills/$name"
    done
  }
  if [[ -d "$root/anthropic-official/plugins" ]]; then
    for plug in "$root/anthropic-official/plugins"/*/skills/*/; do
      [[ -d "$plug" ]] || continue
      local name; name="$(basename "$plug")"
      if [[ -e "$root/$name" ]]; then
        c_green "  skip (existed): $name"
        continue
      fi
      local rel
      rel="$(python3 -c "import os.path,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$plug" "$root")"
      run "ln -s \"$rel\" \"$root/$name\""
      c_green "  flatten: $name -> $rel"
    done
  fi
}

clone_or_update() {
  local repo="$1" dest="$2"
  if [[ -d "$dest/.git" ]]; then
    c_blue "  更新：$dest"
    run "git -C \"$dest\" pull --ff-only"
  else
    c_blue "  克隆：$repo -> $dest"
    run "git clone --depth=1 \"$repo\" \"$dest\""
  fi
}

main() {
  c_blue "=========================================="
  c_blue "  Claude + Codex 跨 agent skill 安装器"
  c_blue "=========================================="
  if [[ "$DRY_RUN" == "1" ]]; then
    c_yellow "DRY_RUN=1 — 只打印不执行"
  fi

  # 检查依赖
  for cmd in git ln readlink; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      c_red "缺少依赖：$cmd"; exit 1
    fi
  done

  # 1. 建立单一真源
  c_blue "[1/4] 准备单一真源目录 $GLOBAL_ROOT"
  run "mkdir -p \"$GLOBAL_ROOT\""

  # 2. 把 Claude / Codex 默认目录软链过去
  c_blue "[2/4] 建立跨 agent 软链"
  ensure_symlink "$CLAUDE_DIR" "$GLOBAL_ROOT"
  ensure_symlink "$CODEX_DIR"  "$GLOBAL_ROOT"

  # 3. clone 核心 skill 仓库
  c_blue "[3/4] 安装核心 skill"
  clone_or_update "https://github.com/obra/superpowers.git"                    "$GLOBAL_ROOT/superpowers"
  clone_or_update "https://github.com/awesome-skills/code-review-skill.git"    "$GLOBAL_ROOT/code-review"
  clone_or_update "https://github.com/anthropics/claude-plugins-official.git"  "$GLOBAL_ROOT/anthropic-official"

  # 4. 项目级软链（在 omni-hub 当前仓库内）
  c_blue "[4/4] 配置当前仓库的项目级 skill"
  if [[ -d "$(pwd)/.agents/skills" ]] || [[ -f "$(pwd)/AGENTS.md" ]]; then
    local proj_agents="$(pwd)/.agents/skills"
    run "mkdir -p \"$proj_agents\""
    ensure_symlink "$(pwd)/.claude/skills" "$proj_agents"
    ensure_symlink "$(pwd)/.codex/skills"  "$proj_agents"
    c_green "  项目层：$(pwd)/.agents/skills/ 已就绪，往里放业务专属 SKILL.md"
  else
    c_yellow "  当前目录不像是 omni-hub 仓库，跳过项目级软链。在仓库根目录下重跑即可。"
  fi

  # 5. 平铺内层 skill（superpowers/skills/* 和 anthropic-official/plugins/*/skills/*）
  c_blue "[5/5] 平铺内层 skill 到顶层（让 agent 一层扫描就能发现）"
  flatten_inner_skills

  c_green ""
  c_green "完成。验证："
  c_green "  ls $CLAUDE_DIR    # 应该能看到 superpowers / code-review / anthropic-official"
  c_green "  ls $CODEX_DIR     # 同上"
  c_green ""
  c_green "在 Claude Code 里：    /plugin list      （或直接说 \"use superpowers\"）"
  c_green "在 Codex CLI 里：       第一句加 \"Follow $GLOBAL_ROOT/superpowers/SKILL.md\""
  if [[ -d "$BACKUP_ROOT" ]]; then
    c_yellow "原目录备份在：$BACKUP_ROOT"
  fi
}

main "$@"
