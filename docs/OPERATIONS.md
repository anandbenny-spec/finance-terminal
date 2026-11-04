# Operations: secrets, accounts and the no-subscription runbook

Written 4 Nov 2026 for survival job S4 (issue #199; Plan 04 §4). The site must keep running after 18 Nov 2026 with no Claude
session, no scheduled Claude task and no API credit. This page lists what it depends on and what to do when something stops.

**This page holds names only.** Never a value, a connection string, a ping URL, a monitor address or an email address. Values
live in Bitwarden (the master copy), and in the places that use them (section 1). If a value ever shows up in the repo,
an issue or a chat, rotate it (section 2 says how), then tell the owner.

Anything you cannot find here: `CLAUDE.md` (the rules), `.env.example` (the names), `.github/workflows/` (the schedules)
and `vercel.json` are the sources this page was written from. When they change, change this page in the same pull request.

## 1. Where a secret can live

| Place | What it holds | Who can read a value |
|---|---|---|
| **Bitwarden** | the master copy of every value below, plus each account's recovery codes | the owner |
| **GitHub Actions secrets** (repository) | what the workflows need; production's and dev's (`DEV_` names) | nobody: write-only after saving |
| **GitHub Actions variable** `PRODUCTION_SCHEDULES` | the switch for scheduled production runs (not a secret) | anyone with repo access |
| **Vercel environment variables** (the one project; the `api` service reads the server-side names, the `web` service the `NEXT_PUBLIC_` ones) | what the live site and API need | the owner, in the dashboard |
| **The Mac's `.env`** (git-ignored, in the app folder) | the names in `.env.example` the owner needs for local runs | the owner |

GitHub's own per-run token (`GITHUB_TOKEN`) is automatic: nothing to set, nothing to renew. `keep-alive.yml` and CI use it.

## 2. Every secret and variable

"Expires" means the credential itself carries an end date. **Two do: `FIXTURES_READ_TOKEN` and `GITHUB_DISPATCH_TOKEN`**
(fine-grained GitHub tokens). The date at the last renewal is recorded on issue #199, never here; the live date is on the token's own
page (GitHub → Settings → Developer settings → Fine-grained tokens).

### 2.1 GitHub Actions secrets: production

| Name | Used by | Expires | If it fails | Rotate |
|---|---|---|---|---|
| `JOB_DATABASE_URL` | `daily`, `weekly`, `watch`, `refresh`, `backfill`, `backfill-check`, `eval-items`, `freshness-drill` (production runs), and `keepalive-db` (always production). Logs in as `job_writer` (shared pooler, session mode) | no | the daily load stops; `/api/health` goes degraded within 36 hours; both monitors email | reset `job_writer`'s password in Supabase, rebuild the URL, update the secret, the Mac `.env` and Bitwarden |
| `STORAGE_ENDPOINT`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY` | `daily`, `weekly`, `watch`, `refresh`, `backfill`, `backfill-check`; raw files go through `ft/storage.py` | no | a run that has to store a file fails, and the last good data stays | create a new S3 access key pair in Supabase's Storage settings, update the three secrets, then revoke the old pair |
| `STORAGE_BUCKET`, `STORAGE_REGION` | the same workflows (one value serves dev and production) | no | as above | not credentials; change only if the bucket moves |
| `FRED_API_KEY` | `policy.market_series` (the daily load every other daily task follows), through `daily`, `weekly`, `watch`, `refresh`, `backfill` | none known; FRED can disable a key | `policy.market_series` raises "FRED_API_KEY is not set" or FRED refuses the key: the daily load fails, so this is the secret whose loss shows first | request a new free key from the St. Louis Fed's API page; update the secret, `.env`, Bitwarden |
| `COINGECKO_DEMO_KEY` | `crypto.coingecko_markets` (`daily`, `refresh`); sent in a header, never in a URL | none known | with no key the task reports "not set up" and skips; a refused key fails that task only | new Demo key in CoinGecko's developer dashboard |
| `SEC_USER_AGENT` | the shared HTTP client, for SEC requests (a declared User-Agent) | no | SEC may throttle an undeclared client | not a credential: an identifying contact string, kept secret so the contact address isn't public; edit the string |
| `ANTHROPIC_API_KEY` | every AI step (`policy.ai_batch` and the 2B tasks) through `ft/ai/client.py` and the Batch API. Scoped to the Console's Default workspace | no; but it stops working when credits end (18 Nov) | the AI steps skip with a recorded reason, never fail the run (section 5.3) | new key in the Console (Default workspace), update the secret and Bitwarden, then disable the old key |
| `VOYAGE_API_KEY` | embeddings in the AI steps. A MongoDB Atlas model API key; the client routes it to `ai.mongodb.com/v1` | no | as `ANTHROPIC_API_KEY`: the chunks wait for a key | new key in Atlas, update the secret and Bitwarden |
| `HEALTHCHECKS_PING_URL` | the last step of `daily.yml` after a successful **production** run; skips quietly with no secret | no | no ping, so the dead-man check goes Down and emails | see section 3 (the monitors) |
| `FIXTURES_READ_TOKEN` | `ci.yml` only: checks out the private `finance-terminal-fixtures` so the saved-page tests run | **yes** (fine-grained token, contents read-only, that one repo) | CI's `test` job fails at the fixtures step (by design: tests must not silently skip). The live site is unaffected | section 4 |

### 2.2 GitHub Actions secrets: dev (manual dev runs only)

`DEV_JOB_DATABASE_URL`, `DEV_STORAGE_ENDPOINT`, `DEV_STORAGE_ACCESS_KEY_ID`, `DEV_STORAGE_SECRET_ACCESS_KEY`. They reach a
step only when a run is started by hand with `target=dev`; a missing one leaves its variable empty and the run stops, never
falling back to production's. The live site does not depend on them. Rotate as their production twins.

### 2.3 GitHub Actions variable

| Name | What it does | If it's wrong |
|---|---|---|
| `PRODUCTION_SCHEDULES` | must be `on` for any **scheduled** run to do anything (`daily`, `weekly`, `watch`, `keep-alive`, `keepalive-db`). Manual runs ignore it | unset or anything else: every scheduled run is skipped without an error. The dead-man check then goes Down in about a day. First thing to check when the monitors email and nothing looks broken |

### 2.4 Vercel environment variables: `api` service (server-side only)

| Name | Used by | Expires | If it fails | Rotate |
|---|---|---|---|---|
| `SUPABASE_URL` | the API's admin check and its Data API reads | no | the admin routes refuse; `/api/health` and the keep-alive answer degraded (503) | not a credential; changes only if the project is replaced |
| `SUPABASE_SERVICE_ROLE_KEY` | the admin routes' refresh calls. **Only ever in Vercel** (`CLAUDE.md` §4) | no | the admin refresh page fails; visitors are unaffected | create a new secret key in Supabase, update Vercel, redeploy, revoke the old |
| `SUPABASE_ANON_KEY` | the daily keep-alive read and `/api/health`. The browser key, the same value as `NEXT_PUBLIC_SUPABASE_ANON_KEY`; never the service-role key | no | `/api/health` answers 503; the keep-alive read fails (the backup `keepalive-db` still runs) | public by design; if replaced, update both names in Vercel and redeploy |
| `CRON_SECRET` | Vercel sends it as `Authorization: Bearer ...` on the daily keep-alive cron | no | with it unset the route answers 503 and the cron's read doesn't happen (the backup still runs) | any new long random value in Vercel, then redeploy |
| `GITHUB_DISPATCH_TOKEN` | the admin refresh button: starts `refresh.yml` through GitHub's API. Fine-grained, Actions: read and write, this repository only | **yes** | the admin refresh button fails ("GitHub answered 401" in the log). Scheduled runs, visitors and the keep-alives are unaffected (visitor refresh isn't built) | section 4 |
| `SQL_READER_DATABASE_URL` | **not read by any code yet** (the command bar is an `after-18-nov` job). Logs in as `sql_reader`, transaction pooler | no | nothing today | reset `sql_reader`'s password when the command bar is built |
| `VISITOR_HASH_SALT` | **not read by any code yet** (same job) | no | nothing today | a new random value (resets visitor hashes) |

Vercel also sets `VERCEL_ENV`, `VERCEL_URL` and `VERCEL_PROJECT_PRODUCTION_URL` itself (the site's own address in link previews;
only the production deployment may dispatch a production run). Nothing to set.

### 2.5 Vercel environment variables: `web` service (public by design)

`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`. Visitors' browsers read them, so they are not secrets, but
Next.js bakes them in at build time: **a change needs a redeploy** before the site uses it. If the anon key is wrong the site
shows "Update delayed" and empty states, not errors.

### 2.6 The Mac's `.env`

The names in `.env.example` the owner needs for local runs, plus `UV_SYSTEM_CERTS` and `NODE_EXTRA_CA_CERTS` on the campus Wi-Fi.
The headless runner (`ops/headless/`) takes the Console key from Bitwarden into a Terminal variable each session and never saves
it. It spends API credit, so it stops being useful when credits end.

## 3. Accounts the site depends on

Each account's recovery codes (and Bitwarden's own) belong in Bitwarden, or for Bitwarden itself on paper: a locked-out password
manager can't hold its own way back in.

| Account | What the project uses it for | Limit that matters | What happens when it's hit |
|---|---|---|---|
| **GitHub** (the app repo, the private fixtures and backup repos) | the code; Actions runs the schedules; the secrets above | Actions minutes are free on a public repo. **Scheduled workflows switch off after 60 days with no repository activity.** Scheduled runs can also start very late, or not at all (28 Oct 2026, #158) | `keep-alive.yml` re-enables the scheduled workflows on the 1st and 15th, using its own token; the `watch` catch-up runs and the dead-man check cover a missed run. If `keep-alive` itself is off: Actions tab → each workflow → Enable |
| **Vercel** (free, Hobby plan; one project, two services) | hosts the site and `/api/*`; one cron | **Cron runs at most once a day, at some point inside the scheduled hour** (`vercel.json`: 03:17 UTC). Hobby is for non-commercial use, which this project is | a cron missed on a given day is covered by `keepalive-db.yml`. Don't add a sub-daily Vercel cron: the deploy fails |
| **Supabase** (free plan; a production and a dev project) | the database, the Data API and file storage | **A free project is paused after a week of low activity** (an email warns about a week before). Free-plan database and storage size caps (see each project's Usage page; the build kept raw files under an 850 MB stop, #265) | the keep-alive (daily Vercel cron plus `keepalive-db` twice a day) prevents the pause. If paused anyway: section 5.5. At a size cap, writes can be refused: the daily load fails and `/api/health` goes degraded |
| **Anthropic Console** | the API key for AI summaries and extraction | prepaid credit; the plan's $100 credit runs to 18 Nov 2026. The pipeline's own $15 monthly switch (`ai_monthly_budget_usd`) stays on | no credit: AI steps skip with the reason "no API credits left"; published AI outputs stay as they are (section 5.3) |
| **MongoDB Atlas** (for Voyage embeddings) | the Voyage model API key; a payment method is on file | billing; keys are managed in Atlas | a refused or unpaid key: embedding steps skip |
| **FRED** (St. Louis Fed) | the free API key for the daily rates and series | FRED's terms let it disable a key or change its API at any time | the daily load fails (the first thing to break); health goes degraded |
| **CoinGecko** | the free Demo key for the crypto daily store | the Demo plan's call quota (10,000 calls a month; the code paces at 30 a minute and the daily snapshot makes 2 calls) | the task skips or fails alone; nothing else depends on it |
| **SEC EDGAR** | no account: a declared User-Agent (`SEC_USER_AGENT`) | SEC's fair-access limit (under 10 requests a second) | throttling only |
| **UptimeRobot** (free) | one **Keyword** monitor on production's `/api/health` (GET; keyword `"status":"ok"`; every 5 minutes; email, no delay, no repeat). Keyword because `/api/health` answers GET only (the free plan locks HEAD) | 50 monitors, 5-minute checks, email alerts, **non-commercial use only** | if the account is closed or the plan changes, there is no outside uptime alert. The dead-man check below still covers the daily load |
| **Healthchecks.io** (free) | one check, "daily production load": Cron `37 6 * * *`, time zone Asia/Kolkata, grace 9 hours, email on | 20 checks | no ping, so no alert on a missed daily run. The check's ping URL is `HEALTHCHECKS_PING_URL`. To rotate it: make a new check with the same settings (or regenerate the URL if the dashboard offers it), update the secret and Bitwarden |
| **Bitwarden** | the master copy of every value and recovery code | account-level | keep an emergency way back in (recovery code on paper) |

No custom domain is in use (the shared link is a `vercel.app` address), so there is no registrar or renewal. If one is added,
list it here with its renewal date.

**What each alert means.**
- **UptimeRobot DOWN:** `/api/health` isn't answering 200 with `"status":"ok"`: the Data API read failed, or the daily load
  (`policy.market_series`) has no success in the last 36 hours.
- **Healthchecks DOWN:** no successful production `daily` run by about 15:37 IST. The morning run is 06:37 IST and GitHub often
  starts it late (up to about 13:15); nine hours of grace cover that. The weekday 16:37 IST run also pings and counts as "up".

**Testing an alert and resetting it** (done on 1 Nov 2026): open the check's ping URL (from Bitwarden) with `/fail` added at the end:
the check goes Down and the email arrives. Then open the plain ping URL once to reset it.

## 4. Token expiry

Both expiring tokens are fine-grained personal access tokens, which must carry an end date (GitHub has capped them at one year; use the longest the form offers).

1. **Read each token's end date** on GitHub (Settings → Developer settings → Fine-grained tokens): `FIXTURES_READ_TOKEN` and
   `GITHUB_DISPATCH_TOKEN`. Record the dates on issue #199, never the values.
2. **`GITHUB_DISPATCH_TOKEN`** can't become a deploy key (it starts workflows). Renew it to the longest the form allows:
   fine-grained, this repository only, Actions: read and write, then replace the value in Vercel, redeploy, and update Bitwarden.
   If it lapses only the admin refresh button breaks.
3. **`FIXTURES_READ_TOKEN`** has two routes.
   - *Renew* to the longest allowed, the same way (contents read-only, the fixtures repo only), then replace the secret.
   - *Replace it with a read-only deploy key* on `finance-terminal-fixtures`, which has no end date: add the key's public half
     in that repo's Settings → Deploy keys (read-only), keep the private half as an Actions secret, switch `ci.yml`'s fixtures
     checkout from `token:` to `ssh-key:`, delete the old token. The deploy-key route removes the only fixed expiry in CI.
4. **Put a reminder in a calendar** two weeks before each remaining end date. A token that lapses fails loudly (CI red, a 401 in the
   refresh log) but only when someone looks.

## 5. The no-subscription runbook

### 5.1 What runs by itself after 18 Nov (all times IST unless stated)

| What | When | Does |
|---|---|---|
| `daily` workflow | 06:37 every day; 16:37 on weekdays | runs the tasks that are due: Fed and RBI documents, the market series, reactions, rate cycles, `policy.ai_batch`, the accuracy counts and the crypto snapshot (`py/config/tasks.yaml` has each task's rule). Then it warms the site's cached pages and pings the dead-man check. The 16:37 run loads only the evening market series and spends no AI |
| `weekly` workflow | Sundays 09:23 | the policy calendar and the Nifty 50 tasks: universe, prices, filings, statements, shareholding, metrics, beta, valuation, the screener snapshot, and the earnings-call tasks (transcripts, text and AI summaries). These are the tasks that call NSE, weekly by design |
| `watch` workflow | FOMC: 13:52 New York time daily, then catch-ups at 15:12 and 17:42 New York time on weekdays. MPC: 09:52 daily, then 11:12 and 13:42 on weekdays | on a decision day, polls for the statement and loads it. Each catch-up run is one pass that loads a decision still missing. On other days there is nothing to do |
| `keepalive-db` workflow | 06:53 and 18:53 | one read-only query on production's database, so Supabase doesn't pause it |
| Vercel cron | once a day, 03:17 UTC (08:47 IST, anywhere in that hour) | `GET /api/cron/keepalive`: one real Data API read |
| `keep-alive` workflow | 04:41 UTC on the 1st and 15th | re-enables the scheduled workflows if GitHub switched them off |
| UptimeRobot | every 5 minutes | `/api/health` |
| Healthchecks.io | expects a ping each day | emails if none arrives |
| The Mac's backup script (launchd) | whenever the Mac is on | the backup routine of #81 |

All scheduled workflows are skipped unless the `PRODUCTION_SCHEDULES` variable is `on` (section 2.3).

### 5.2 What stops

- **Claude's scheduled tasks:** the build chains (lanes A, B and O), the weekly drift check, the every-two-days backup task (#81)
  and any one-off task. None of them is needed for the site to run.
- **Headless runs on the Mac** (`ops/headless/`): they bill the Console API key.
- **AI steps, once credits end** (section 5.3).
- **Building:** no one reads `CLAUDE.md`'s queue until a plan returns (section 5.6).

Take one last manual backup before 18 Nov, because the scheduled half of the backup routine stops that day.

### 5.3 AI with no key or credits (S2, #197)

Nothing the live site needs depends on Claude. The AI work is precomputed in batches and stored; **the site makes no live AI call for a
visitor** (the command bar, the one feature that would, is an `after-18-nov` job: revisit this section when it is built). When the key is missing, refused, or out of credit, each AI step skips with a recorded reason ("no API key", "the API
key was refused", "no API credits left"): it fails no run, sends nothing and loses nothing. Outputs already published stay
published. Documents stored from then on wait for their summaries until a key and credits return, and the pages show labelled
states instead of errors. To switch AI off deliberately for a manual run, set the `ai` input to `off` on `daily` or `watch`.

### 5.4 What breaks first, and how it shows

1. **The daily load fails**: the FRED key, a source's page layout changing, a database or storage problem. Shows as "Update delayed"
   on the site, `/api/health` going degraded, and both monitors emailing. Open the Actions tab, read the failed `daily` run's
   annotations, fix or wait, then run `daily` by hand (5.7).
2. **A new committee member.** `py/config/members.yaml` is curated by hand and fails closed: the blocking check
   `voters_on_roster` rolls the load back when a statement or the minutes name someone the roster doesn't know, so the
   last good data stays and the page shows "Update delayed". The FOMC's voting presidents change each January (each voting
   year is its own term in the file); MPC membership changes when the government appoints. It needs a human edit, with a source note per term.
3. **An unscheduled FOMC meeting** needs a row in `py/config/meeting_times.yaml` (the calendar task fails without one, by design).
   Scheduled meetings need nothing.
4. **Token end dates** (section 4): CI and the admin refresh button, not the site.
5. **A free-tier limit** (section 3): Supabase size, an expired Anthropic or Atlas payment, CoinGecko's monthly quota.
6. **A source objects.** Anything an owner objects to is removed at once (`core.source.is_suppressed`), with BSE as the NSE fallback.

### 5.5 Restoring a paused Supabase project

1. Supabase dashboard → the organisation → the paused project → **Resume project**, and confirm. A paused project is
   restorable for a limited window (Supabase's pages have stated 90 days and, in a later version, one year): don't count on the longer
   one, and resume as soon as the pause email arrives.
2. Wait for it to come back, then open `/api/health` and the site.
3. Run `daily` against production by hand (5.7) so the days it missed are loaded; the due rules catch up.
4. If the dead-man check went Down meanwhile, the next successful `daily` run pings it back up.
5. Check the cause: the keep-alive (a Vercel cron every day, `keepalive-db` twice a day) should have prevented this. Read
   the last runs of `keepalive-db` and the cron's log in Vercel, and that `PRODUCTION_SCHEDULES` is still `on`.

### 5.6 Resuming building later

Start a Claude plan again (Claude Code on the web or the desktop app), open a session on this repo and type `/next-job`: it takes the
next job from tracker issue #30, one session per lane. The planning documents live in the claude.ai project **Finance Terminal**
(`CLAUDE.md` §8 names the docs); the backlog of jobs held back for now carries the `after-18-nov` label. Migrations still need the
owner's `supabase db push`, and the rules in `CLAUDE.md` §7 still stop for him.

### 5.7 Starting a run by hand

From GitHub's Actions tab (workflow → Run workflow), or the CLI. A manual run targets **dev** unless `production` is chosen:

```bash
gh workflow run daily.yml --repo anandbenny-spec/finance-terminal -f target=production
gh workflow run daily.yml --repo anandbenny-spec/finance-terminal -f target=production -f ai=off
gh run list --repo anandbenny-spec/finance-terminal --workflow daily.yml --limit 5
```

### 5.8 Monthly check (ten minutes, the first of the month)

- The two monitors' emails: any Down since last month? Open both dashboards: green.
- Actions tab: the latest `daily`, `weekly` and `keepalive-db` runs are green, and none of the scheduled workflows shows "disabled".
- `PRODUCTION_SCHEDULES` is still `on`.
- Supabase: no pause warning; the Usage page is below the caps. Vercel: the latest production deployment is Ready.
- Token end dates (section 4): none within 30 days.
- Credits: the Anthropic Console's balance, if AI outputs are wanted to keep updating.
- A calendar entry for the next 60-day mark, in case `keep-alive` itself has stopped.

## 6. Bitwarden

Bitwarden holds every name in section 2 that has a value (the `DEV_` ones too), and recovery codes for GitHub (2FA), Supabase,
Vercel, the Anthropic Console, MongoDB Atlas, UptimeRobot and Healthchecks.io. The check is the owner's: each name in section 2 against its Bitwarden entry,
and a test sign-in path for each account that doesn't depend on the device that is signed in today.
