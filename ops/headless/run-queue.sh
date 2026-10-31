#!/usr/bin/env bash
# Runs several headless jobs one after another (issue #195).
# Usage: run-queue.sh <issue> <issue> ...
# Stops when the ledger total reaches FT_HEADLESS_CAP (default 80 dollars), when a run fails,
# or when the issue it just ran is labelled needs-anand.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: run-queue.sh <issue> <issue> ..." >&2
  exit 2
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${FT_HEADLESS_BASE:-$HOME/Projects/finance-terminal}"
LOGS="${FT_HEADLESS_LOGS:-$BASE/headless-logs}"
LEDGER="$LOGS/ledger.csv"
CAP="${FT_HEADLESS_CAP:-80}"
REPO="anandbenny-spec/finance-terminal"

total() {
  if [ -f "$LEDGER" ]; then
    awk -F, 'NR>1 {s+=$3} END {printf "%.2f", s}' "$LEDGER"
  else
    echo "0.00"
  fi
}

if command -v caffeinate >/dev/null 2>&1 && [ -z "${FT_QUEUE_AWAKE:-}" ]; then
  FT_QUEUE_AWAKE=1 exec caffeinate -i "$0" "$@"
fi

for ISSUE in "$@"; do
  if awk -v t="$(total)" -v c="$CAP" 'BEGIN {exit !(t+0 >= c+0)}'; then
    echo "run-queue: the ledger total \$$(total) has reached the cap \$$CAP. Stopping before #$ISSUE."
    exit 0
  fi
  echo "run-queue: starting #$ISSUE"
  if ! "$HERE/run-job.sh" "$ISSUE"; then
    echo "run-queue: the run for #$ISSUE failed. Stopping." >&2
    exit 1
  fi
  if gh api "repos/$REPO/issues/$ISSUE" --jq '[.labels[].name] | index("needs-anand") != null' | grep -q true; then
    echo "run-queue: #$ISSUE is labelled needs-anand. Stopping."
    exit 0
  fi
done
echo "run-queue: done. Ledger total \$$(total)."
