#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-$(pwd)}"
BASE_REF="${STRYKER_CXX_REVIEW_BASE:-origin/main}"
TARGET_REPO="${STRYKER_CXX_REVIEW_TARGET_REPO:-anagnorisis2peripeteia/stryker-cxx}"
CEILING="${STRYKER_CXX_REVIEW_CEILING:-agy-gemini}"

if [[ ! -d "$ROOT_DIR/.git" ]]; then
  echo "error: $ROOT_DIR is not a git repository" >&2
  exit 1
fi

if [[ ! -x /Users/cameronbeeley/.claude/skills/ask-opencode/bin/review-cascade ]]; then
  echo "error: review-cascade not found at /Users/cameronbeeley/.claude/skills/ask-opencode/bin/review-cascade" >&2
  exit 1
fi

/Users/cameronbeeley/.claude/skills/ask-opencode/bin/review-cascade \
  --target_repo "$TARGET_REPO" \
  --target-dir "$ROOT_DIR" \
  --base "$BASE_REF" \
  --ceiling "$CEILING"
