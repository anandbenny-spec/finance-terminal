# Headless build runs on the Mac

Runs one `headless` build job (or a queue of them) with `claude -p` on Sonnet. The run is billed to the Console API key, not the weekly plan: `--bare` never reads the Mac's subscription login. It works in its own clone, never in `~/Projects/finance-terminal/app`. Nothing here runs on a schedule: Anand starts each run from his own Terminal.

Written against `claude --help` on Claude Code 2.1.314 (flags checked there; the first real run is the live test). The flags used (`--bare`, `-p`, `--append-system-prompt-file`, `--model`, `--permission-mode`, `--permission-prompts`, `--allowedTools`, `--max-budget-usd`, `--output-format`) need a version that has `--permission-prompts` (2.1.259 or later). Check with `claude --version`.

## One-time setup

Install Claude Code, then the tools the runner calls:

```bash
brew install gh jq gitleaks
gh auth login
```

The runner clones `anandbenny-spec/finance-terminal` into `~/Projects/finance-terminal/headless` and the private fixtures into its `data/fixtures` the first time it runs. Override the places with `FT_HEADLESS_BASE`, `FT_HEADLESS_DIR` or `FT_HEADLESS_LOGS`. The clone's folder must not be `app/`; the script refuses.

On the campus Wi-Fi (Sophos) set these first:

```bash
export UV_SYSTEM_CERTS=true
export NODE_EXTRA_CA_CERTS=/path/to/the/sophos-ca.pem
```

## Each Terminal session

Paste the key from Bitwarden when asked. It is never saved to a file or printed:

```bash
read -s ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY
```

## Run one job

The budget is optional and defaults to 6 dollars per run:

```bash
~/Projects/finance-terminal/app/ops/headless/run-job.sh 196
~/Projects/finance-terminal/app/ops/headless/run-job.sh 196 4
```

Only an issue labelled `headless` is taken. The run claims it, builds it on its `claude/` branch, opens the pull request, waits for CI, merges when green and writes the handoff on the issue.

## Run a queue

Runs the issues one after another under `caffeinate -i`. It stops when the ledger total reaches `FT_HEADLESS_CAP` (default 80 dollars), when a run fails, or when the issue it just ran is labelled `needs-anand`:

```bash
~/Projects/finance-terminal/app/ops/headless/run-queue.sh 196 198 197
```

**Which jobs to queue (1 Nov 2026):** jobs labelled `headless` from tracker #30's order. Module (c)'s jobs are lane C (`lane-c`), which no build chain takes, so this runner builds them, in lane C's order. Queue only jobs whose dependencies are merged, or will be by the time they run: each run checks its card's "Depends on" row first and stops with a comment if one isn't met. One run at a time for all lanes: the lock below is shared.

## Where the numbers are

Each run's JSON is saved as `~/Projects/finance-terminal/headless-logs/job-<issue>-<UTC time>.json`. One line per run goes to `~/Projects/finance-terminal/headless-logs/ledger.csv`: UTC time, issue, `total_cost_usd`, exit code, `is_error`, session id. The script prints the running total after each run.

## Stop a run

Press Ctrl-C in the Terminal. If the next run says another run holds the lock and none is running, remove the folder `~/Projects/finance-terminal/headless-logs/run.lock`.

## What the run may do

The allowlist is `git`, `gh`, `uv`, `npm`, `npx`, `jq`, `sleep`, `cd`, and the read-only `ls`, `cat`, `grep`, `head`, `tail`, `wc`, `find`, `diff`, `sed -n`, plus the file tools (Read, Write, Edit, Glob, Grep). Anything else is denied automatically, so a job that needs more stops with a comment instead of asking. `--dangerously-skip-permissions` is never used. A compound command such as `cd py && uv run ...` is allowed when every part is on the list; a command that isn't (`rm`, `curl`, `python`) is denied. Edit the `ALLOWED` line in `run-job.sh` to widen it, deliberately.
