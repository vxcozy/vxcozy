#!/usr/bin/env bash
# Regenerate the Claude Code activity card and commit/push if it changed.
#
# Designed to run on the aggregator Mac (the one with all three machines'
# session data synced locally). Configure paths via env vars below, or
# override them in the launchd plist.
#
# Env vars (all optional; sensible defaults for a Mac aggregator):
#   REPO_DIR        Path to the vxcozy repo checkout.
#   BRANCH          Branch to pull/commit/push. Defaults to "main".
#   CLAUDE_DIRS     Colon-separated list of ~/.claude-style dirs to scan.
#   DESKTOP_DIRS    Colon-separated list of claude-code-sessions dirs to scan.
#   PYTHON          Python interpreter. Defaults to "python3".
#
# Exits 0 on success (whether or not a commit was made).

set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/Documents/GitHub/vxcozy}"
BRANCH="${BRANCH:-main}"
PYTHON="${PYTHON:-python3}"

# Defaults assume: local data + two synced machines under ~/synced/<hostname>/
CLAUDE_DIRS="${CLAUDE_DIRS:-$HOME/.claude:$HOME/synced/work-laptop/.claude:$HOME/synced/personal-laptop/.claude}"
DESKTOP_DIRS="${DESKTOP_DIRS:-$HOME/Library/Application Support/Claude/claude-code-sessions:$HOME/synced/work-laptop/claude-code-sessions:$HOME/synced/personal-laptop/claude-code-sessions}"

cd "$REPO_DIR"

# Sync with remote before regenerating so we don't race the contributions workflow.
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

# Build --dir / --desktop-dir flags from the colon-separated env vars.
dir_args=()
IFS=':' read -ra _dirs <<< "$CLAUDE_DIRS"
for d in "${_dirs[@]}"; do
  [[ -n "$d" ]] && dir_args+=(--dir "$d")
done

desktop_args=()
IFS=':' read -ra _desktops <<< "$DESKTOP_DIRS"
for d in "${_desktops[@]}"; do
  [[ -n "$d" ]] && desktop_args+=(--desktop-dir "$d")
done

"$PYTHON" tools/claude-stats.py \
  "${dir_args[@]}" \
  "${desktop_args[@]}" \
  --out ./graph

# Only commit if something actually changed.
if git diff --quiet -- graph/claude-stats.json graph/claude-card.svg; then
  echo "no stats changes"
  # Flush any commits a previous run made but failed to push (e.g. transient
  # auth/network failure). Without this, identical regen output would mask a
  # stale local branch forever.
  if [[ -n "$(git rev-list "origin/$BRANCH..HEAD" 2>/dev/null)" ]]; then
    echo "pushing pending local commits"
    git push origin "$BRANCH"
  fi
  exit 0
fi

git add graph/claude-stats.json graph/claude-card.svg
git commit -m "update claude code activity card"
git push origin "$BRANCH"
