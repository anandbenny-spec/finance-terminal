#!/usr/bin/env bash
# Runs one build job headless on the Mac: claude -p, Sonnet, billed to the API key (issue #195).
# Usage: run-job.sh <issue> [budget-usd, default 6]
set -euo pipefail

ISSUE="${1:?usage: run-job.sh <issue> [budget-usd]}"
BUDGET="${2:-6}"
case "$ISSUE" in ''|*[!0-9]*) echo "run-job: the issue must be a number" >&2; exit 2 ;; esac
case "$BUDGET" in ''|*[!0-9.]*) echo "run-job: the budget must be a number of dollars" >&2; exit 2 ;; esac

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "run-job: ANTHROPIC_API_KEY isn't set. See ops/headless/README.md." >&2
  exit 1
fi

REPO="anandbenny-spec/finance-terminal"
BASE="${FT_HEADLESS_BASE:-$HOME/Projects/finance-terminal}"
DIR="${FT_HEADLESS_DIR:-$BASE/headless}"
LOGS="${FT_HEADLESS_LOGS:-$BASE/headless-logs}"
LOCK="$LOGS/run.lock"
APP="$BASE/app"

case "$(cd "$DIR" 2>/dev/null && pwd -P || echo "$DIR")" in
  "$APP"|"$APP"/*) echo "run-job: the headless clone must never be Anand's app/ folder" >&2; exit 1 ;;
esac

mkdir -p "$LOGS"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "run-job: another headless run holds $LOCK. If none is running, remove that folder." >&2
  exit 1
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [ ! -d "$DIR/.git" ]; then
  mkdir -p "$(dirname "$DIR")"
  gh repo clone "$REPO" "$DIR"
fi
cd "$DIR"
git config core.hooksPath .githooks
git config user.name "Claude (headless)"
git config user.email "noreply@anthropic.com"
if [ ! -d data/fixtures/.git ]; then
  mkdir -p data
  gh repo clone anandbenny-spec/finance-terminal-fixtures data/fixtures -- --depth 1
fi
git fetch origin
git checkout main
git reset --hard origin/main

PROMPT="$(sed "s/{{ISSUE}}/$ISSUE/g" ops/headless/job-prompt.md)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$LOGS/job-$ISSUE-$STAMP.json"
ALLOWED="Bash(git:*),Bash(gh:*),Bash(uv:*),Bash(npm:*),Bash(npx:*),Bash(jq:*),Bash(sleep:*),Bash(ls:*),Bash(cat:*),Bash(grep:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(find:*),Bash(diff:*),Bash(sed -n:*),Bash(cd:*),Bash(pwd),Read,Write,Edit,Glob,Grep"

set +e
claude --bare -p "$PROMPT" \
  --append-system-prompt-file CLAUDE.md \
  --model sonnet \
  --permission-mode acceptEdits \
  --permission-prompts none \
  --allowedTools "$ALLOWED" \
  --max-budget-usd "$BUDGET" \
  --output-format json > "$OUT"
CODE=$?
set -e

COST="$(jq -r '.total_cost_usd // 0' "$OUT" 2>/dev/null || echo 0)"
IS_ERR="$(jq -r '.is_error // true' "$OUT" 2>/dev/null || echo true)"
SESSION="$(jq -r '.session_id // ""' "$OUT" 2>/dev/null || echo "")"
LEDGER="$LOGS/ledger.csv"
[ -f "$LEDGER" ] || echo "utc_time,issue,total_cost_usd,exit_code,is_error,session_id" > "$LEDGER"
echo "$STAMP,$ISSUE,$COST,$CODE,$IS_ERR,$SESSION" >> "$LEDGER"
TOTAL="$(awk -F, 'NR>1 {s+=$3} END {printf "%.2f", s}' "$LEDGER")"
echo "run-job: issue #$ISSUE cost \$$COST, exit $CODE, is_error $IS_ERR. Ledger total \$$TOTAL. Log: $OUT"
exit "$CODE"
