# Operations: secrets, accounts and the no-subscription runbook

Written 4 Nov 2026 for survival job S4 (issue #199; Plan 04 §4). The site must keep running after 18 Nov 2026 with no Claude
session, no scheduled Claude task and no API credit. This page lists what it depends on and what to do when something stops.

**This page holds names only.** Never a value, a connection string, a ping URL, a monitor address or an email address. Values
live in Bitwarden (the master copy of every credential that can't be read back elsewhere, section 6), and in the places that use
them (section 1). If a value ever shows up in the repo, an issue or a chat, rotate it (section 2 says how), then tell the owner.

Anything you cannot find here: `CLAUDE.md` (the rules), `.env.example` (the names), `.github/workflows/` (the schedules)
and `vercel.json` are the sources this page was written from. When they change, change this page in the same pull request.

## 1. Where a secret can live

| Place | What it holds | Who can read a value |
|---|---|---|
| **Bitwarden** | the master copy of every credential that can't be read back elsewhere (section 6 lists the few that are re-read from a dashboard instead), plus each account's recovery codes | the owner |
| **GitHub Actions secrets** (repository) | what the workflows need; production's and dev's (`DEV_` names) | nobody: write-only after saving |
| **GitHub Actions variable** `PRODUCTION_SCHEDULES` | the switch for scheduled production runs (not a secret) | anyone with repo access |
| **Vercel environment variables** (the one project; the `api` service reads the server-side names, the `web` service the `NEXT_PUBLIC_` ones) | what the live site and API need | the owner, in the dashboard |
| **The Mac's `.env`** (git-ignored, in the app folder) | the names in `.env.example` the owner needs for local runs | the owner |

GitHub's own per-run token (`GITHUB_TOKEN`) is automatic: nothing to set, nothing to renew. `keep-alive.yml` and CI use it.

## 2. Every secret and variable

"Expires" means the credential carries an end date. **None does.** Both fine-grained GitHub tokens the project has used were
created with no end date (read on their GitHub pages, 4 Nov 2026), and the one still in use is `GITHUB_DISPATCH_TOKEN`. What can
still end a fine-grained token is **idle revocation**: GitHub revokes one that goes unused for a year (section 4). CI's read access to
the private fixtures repo, which used a second token (`FIXTURES_READ_TOKEN`), became a read-only deploy key on 4 Nov 2026.

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
| `FIXTURES_DEPLOY_KEY` | `ci.yml` only: the private half of a read-only deploy key on `finance-terminal-fixtures`; the `test` job checks that repo out with it (`ssh-key:`) so the saved-page tests run | no (a deploy key has no end date) | CI's `test` job fails at the fixtures step (by design: tests must not silently skip), if the secret is missing or the key was removed from that repo. The live site is unaffected | make a new key pair, add its public half as a **read-only** deploy key on the fixtures repo, update the secret and Bitwarden, then delete the old deploy key there |

### 2.2 GitHub Actions secrets: dev (manual dev runs only)

`DEV_JOB_DATABASE_URL`, `DEV_STORAGE_ENDPOINT`, `DEV_STORAGE_ACCESS_KEY_ID`, `DEV_STORAGE_SECRET_ACCESS_KEY`. They reach a
step only when a run is started by hand with `target=dev`; a missing one leaves its variable empty and the run stops, never
falling back to production's. The live site does not depend on them. Rotate as their production twins.

### 2.3 GitHub Actions variable

| Name | What it does | If it's wrong |
|---|---|---|
| `PRODUCTION_SCHEDULES` | must be `on` for any **scheduled** run to do anything (`daily`, `weekly`, `watch`, `keep-alive`, `keepalive-db`). Manual runs ignore it | unset or anything else: every scheduled run is skipped without an error. The dead-man check then goes Down in about a day. First thing to check when the monitors email and nothing looks broken |

**Presence check, 4 Nov 2026 (#199).** `gh secret list` showed 17 secrets, `gh variable list` showed `PRODUCTION_SCHEDULES`: every
name in sections 2.1 to 2.3 is set and nothing else is. The latest scheduled runs of `daily`, `keepalive-db`, `weekly` and `keep-alive`
had all succeeded, which also shows `PRODUCTION_SCHEDULES` is `on`.

### 2.4 Vercel environment variables: `api` service (server-side only)

| Name | Used by | Expires | If it fails | Rotate |
|---|---|---|---|---|
| `SUPABASE_URL` | the API's admin check and its Data API reads | no | the admin routes refuse; `/api/health` and the keep-alive answer degraded (503) | not a credential; changes only if the project is replaced |
| `SUPABASE_SERVICE_ROLE_KEY` | the admin routes' refresh calls. **Only ever in Vercel** (`CLAUDE.md` §4) | no | the admin refresh page fails; visitors are unaffected | create a new secret key in Supabase, update Vercel, redeploy, revoke the old |
| `SUPABASE_ANON_KEY` | the daily keep-alive read and `/api/health`. The browser key, the same value as `NEXT_PUBLIC_SUPABASE_ANON_KEY`; never the service-role key | no | `/api/health` answers 503; the keep-alive read fails (the backup `keepalive-db` still runs) | public by design; if replaced, update both names in Vercel and redeploy |
| `CRON_SECRET` | Vercel sends it as `Authorization: Bearer ...` on the daily keep-alive cron | no | with it unset the route answers 503 and the cron's read doesn't happen (the backup still runs) | any new long random value in Vercel, then redeploy |
| `GITHUB_DISPATCH_TOKEN` | the admin refresh button: starts `refresh.yml` through GitHub's API. Fine-grained, Actions: read and write, this repository only | no end date (checked 4 Nov 2026); GitHub revokes it if it goes unused for a year | the admin refresh button fails ("GitHub answered 401" in the log). Scheduled runs, visitors and the keep-alives are unaffected (visitor refresh isn't built) | section 4 |
| `SQL_READER_DATABASE_URL` | **not read by any code yet** (the command bar is an `after-18-nov` job). Logs in as `sql_reader`, transaction pooler | no | nothing today | reset `sql_reader`'s password when the command bar is built |
| `VISITOR_HASH_SALT` | **not read by any code yet** (same job) | no | nothing today | a new random value (resets visitor hashes) |

Vercel also sets `VERCEL_ENV`, `VERCEL_URL` and `VERCEL_PROJECT_PRODUCTION_URL` itself (the site's own address in link previews;
only the production deployment may dispatch a production run). Nothing to set.

### 2.5 Vercel environment variables: `web` service (public by design)

`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`. Visitors' browsers read them, so they are not secrets, but
Next.js bakes them in at build time: **a change needs a redeploy** before the site uses it. If the anon key is wrong the site
shows "Update delayed" and empty states, not errors.

**Presence check, 4 Nov 2026 (#199), from the Vercel dashboard.** All nine names of sections 2.4 and 2.5 are set for **Production**, and
nothing else is. `SUPABASE_ANON_KEY` and `CRON_SECRET` are Production-only (a preview deployment's health and keep-alive routes
therefore answer 503, which is fine). `GITHUB_DISPATCH_TOKEN`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SQL_READER_DATABASE_URL`,
`VISITOR_HASH_SALT` and the two `NEXT_PUBLIC_` names are also set for **Preview**.

**Previews use the dev project (checked 4 Nov 2026, #199).** `SUPABASE_URL` has a separate row for Preview, and its value is the dev
project's; the Production row holds production's. Only the URL could be compared: Vercel hides a saved secret, so the Preview copies
of `SUPABASE_SERVICE_ROLE_KEY` and `SQL_READER_DATABASE_URL` can't be read back. When one of those is replaced, set Preview's copy from
the **dev** project and Production's from production, never the other way round, and never merge the two rows into one.

### 2.6 The Mac's `.env`

The names in `.env.example` the owner needs for local runs, plus `UV_SYSTEM_CERTS` and `NODE_EXTRA_CA_CERTS` on the campus Wi-Fi.
It points at the **dev** project (checked 4 Nov 2026: its `SUPABASE_URL` carries the dev project's reference), so a local run never
touches production. Production's connection strings are not kept on the Mac.
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
| **Bitwarden** | the master copy of the credentials that can't be read back elsewhere, and the recovery codes | account-level | keep an emergency way back in (recovery code on paper) |

No custom domain is in use (the shared link is a `vercel.app` address), so there is no registrar or renewal. If one is added,
list it here with its renewal date.

**What each alert means.**
- **UptimeRobot DOWN:** `/api/health` isn't answering 200 with `"status":"ok"`: the Data API read failed, or the daily load
  (`policy.market_series`) has no success in the last 36 hours.
- **Healthchecks DOWN:** no successful production `daily` run by about 15:37 IST. The morning run is due at 06:37 IST, and GitHub
  starts it very late: on 3 and 4 Nov 2026 it started at about 13:18 and 12:56 IST and finished by about 13:28 and 13:11, which leaves
  about two hours inside the nine hours of grace. The weekday 16:37 IST run also pings and counts as "up", but it has started
  hours late too (about 23:54 IST on 3 Nov), so don't rely on it. A single Down email on a late day is worth a look at the Actions
  tab before anything else: if the run is queued or running, wait.

**Testing an alert and resetting it** (done on 1 Nov 2026): open the check's ping URL (from Bitwarden) with `/fail` added at the end:
the check goes Down and the email arrives. Then open the plain ping URL once to reset it.

## 4. Tokens: end dates and idle revocation

**No credential carries an end date.** On 4 Nov 2026 both fine-grained tokens showed "no expiration date" on their GitHub pages
(Settings → Developer settings → Fine-grained tokens): `GITHUB_DISPATCH_TOKEN` ("finance-terminal refresh dispatch") and the one
CI used for the fixtures repo until the same day (replaced, below). There is nothing to renew on a schedule.

What can still end one is **idle revocation**. GitHub's page on credential types says a fine-grained token is revoked
automatically if it goes unused for a year, or if it is ever pushed to a public repository.
`GITHUB_DISPATCH_TOKEN` is used only when someone clicks the admin refresh button, so once building stops it can sit unused.

1. **Check it** in the monthly check (5.8): the token still appears on that GitHub page, and "Last used" is recent.
2. **If it was revoked**, only the admin refresh button breaks (a 401 in the refresh log; the site, the scheduled runs and the
   keep-alives are unaffected). Make a new fine-grained token: this repository only, Actions: read and write. Replace the value in
   Vercel, redeploy, and update Bitwarden. Using the token within the year avoids this.

**CI's fixtures access was replaced** (Anand, 4 Nov 2026, #199): the `test` job now reads the private fixtures repo with the
read-only deploy key `FIXTURES_DEPLOY_KEY` (section 2.1) instead of the fine-grained token `FIXTURES_READ_TOKEN`. A deploy key
belongs to that one repository, has no end date, and GitHub's credential-types page names no idle rule for it. To rotate it:
generate a new key pair, add the public half to the fixtures repo's Deploy keys (read-only, never "allow write"), update the
Actions secret and Bitwarden, and delete the old deploy key there.

## 5. The no-subscription runbook

### 5.1 What runs by itself after 18 Nov (all times IST unless stated)

| What | When | Does |
|---|---|---|
| `daily` workflow | 06:37 every day; 16:37 on weekdays | runs the tasks that are due: Fed and RBI documents, the market series, reactions, rate cycles, `policy.ai_batch`, the accuracy counts and the crypto snapshot (`py/config/tasks.yaml` has each task's rule). Then it warms the site's cached pages and pings the dead-man check. The 16:37 run loads only the evening market series and spends no AI |
| `weekly` workflow | Sundays 09:23 | the policy calendar and the Nifty 50 tasks: universe, prices, filings, statements, shareholding, metrics, beta, valuation, the screener snapshot, and the earnings-call tasks (transcripts, text and AI summaries; a summary only for each company's newest call, so a new prompt version never re-summarises the history on its own: only a `backfill` with dates does, for the calls filed in them, #370). These are the tasks that call NSE, weekly by design |
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
4. **A token revoked for sitting unused** (section 4): only the admin refresh button, never the site or the scheduled runs.
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
- Supabase: no pause warning; the Usage page is below the caps, and the raw bucket is under 850 MB (section 5.9). Vercel: the latest production deployment is Ready.
- The admin refresh token (section 4) is still listed on its GitHub page, with a recent "Last used".
- Credits: the Anthropic Console's balance, if AI outputs are wanted to keep updating.
- A calendar entry for the next 60-day mark, in case `keep-alive` itself has stopped.

### 5.9 When the bucket nears 850 MB

The raw bucket holds every stored source file: earnings-call PDFs, NSE replies, filings, and the Fed and RBI documents.
Supabase's Free plan allows 1 GB of file storage per project. The build set a stop at **850 MB** (`raw_storage_stop_mb` in
`py/config/equity/calls.yaml`, 85% of the cap; Plan 04b P5, #265). Nothing else watches the size.

**What the stop does, and what it doesn't.** The `raw_storage` check of `equity.call_transcripts` (a `weekly` task) compares
the bucket with the stop. It is a warning, not a brake: over the stop the run still succeeds and still stores files. The only
trace is an open alert, `check:equity.call_transcripts:raw_storage`, on the admin Health page (`/admin/health`) and in
`ops.alert`. It sends no email, and `/api/health` does not read it, so both monitors stay green (section 5.1). The hard limit
is Supabase's own cap: at it, writes can be refused and the daily load fails too (section 3, Supabase row). So 850 MB is a
prompt to act, not the point where harm is prevented.

**How fast it grows** (production, measured 8 Nov 2026; measure again rather than trust this). The bucket held 269 MB before
the earnings-call history was loaded and 676 MiB after it: about 300 calls, about 400 MB. Each results season (four a year)
adds roughly 70 MB, and the weekly runs add small files between seasons. From 676 MiB, 850 MB is about two seasons away. The
dashboard's Usage page read 0.43 GB on the same day; the gap was not explained, so take the larger figure.

**Read the size** (production's SQL editor; read-only), in the monthly check (5.8) and whenever the alert above is open:

```sql
select bucket_id, count(*) as objects, round(sum((metadata->>'size')::bigint) / 1048576.0, 1) as mib
from storage.objects
group by bucket_id
order by 3 desc;
```

**The three exits.** Each is the owner's decision (`CLAUDE.md` §7); a session does not take one alone.

1. **Cloudflare R2.** New raw files go to R2. Every stored object is copied, checked by hash and kept in Supabase as well.
   - *State:* built, parked and unmerged: branch `claude/2b-01-r2` (#164). Merging it changes nothing until a target's line in
     `py/config/storage.yaml` (the branch adds the file) is flipped, and that happens only after the copy verifies with
     0 mismatches.
   - *Needs:* a Cloudflare account and buckets, two bucket-scoped tokens, and eight GitHub secrets (`R2_ENDPOINT`,
     `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and the same four with a `DEV_` prefix), possibly a payment
     method. A session merges `main` into the branch first: it was 9 days behind on 8 Nov, and `ft/storage.py` is on every
     job's path.
   - *Buys:* new files stop counting against the 1 GB. *The catch:* one more service to keep alive with no subscription,
     which is why it was parked (30 Oct and 8 Nov 2026). Deleting the Supabase copies afterwards is a separate, irreversible
     decision.
2. **A higher stop.** Raise `raw_storage_stop_mb` in `py/config/equity/calls.yaml` in a pull request.
   - *Needs:* the owner's word, with the reason written next to the line (it loosens a check).
   - *Buys:* about one more season at the 8 Nov rate. *The catch:* it only moves the warning. Supabase's 1 GB cap stays, and
     the daily load shares the bucket, so leave room for it.
3. **Supabase Pro.** Move production's organisation to a paid plan.
   - *Needs:* the owner's decision (a paid plan) and no code. Production has had its own organisation since 9 Nov 2026, so it
     applies to that one, not dev's.
   - *Buys:* a larger allowance, so the stop can then be raised as in exit 2. *The catch:* a running cost.

**Do not** delete objects or `core.raw_file` rows to make room: `job_writer` cannot, source data is never deleted
(`CLAUDE.md` §3), and deleting stored objects is irreversible (§7). Do not point `ft/storage.py` at another store by hand.

**After an exit,** read the size again with the query above, start `weekly` by hand (5.7), and check that the
`raw_storage` alert closes: the next run that passes the check resolves it.

### 5.10 Incident record: call-transcript summaries requested in error (8 to 10 Nov 2026; #351, #356)

From 8 Nov 2026 `policy.ai_batch` asked for `doc_summary` outputs on earnings-call transcripts, which belong to module (c)'s tasks. The production `daily` hung and was cancelled at 30 minutes each time until the fix (#353, 10 Nov 2026). 604 summaries were requested in error; every one was rejected and none was ever shown to a visitor. Since #356 the `citation_integrity` month share counts only the Fed's and the RBI's documents, and `site.quality_summary` skips `doc_summary` outputs whose target is a call transcript, so these rows no longer skew either; the rows themselves stay in `ops.ai_request` and `core.ai_output` as the record.

## 6. Bitwarden

**What Bitwarden holds.** Every credential that cannot be read back from somewhere else, plus the recovery codes:

- **Per Supabase project (production and dev):** the `postgres` password, the admin login, and the `job_writer` and `sql_reader`
  passwords (each entry named for its project and role).
- **S3 key pairs:** production's `github-jobs`, dev's `github-jobs`, and the Mac's `mac-dev`. Supabase shows an S3 secret once.
- **Single-value secrets:** `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `FRED_API_KEY`, `COINGECKO_DEMO_KEY`, `HEALTHCHECKS_PING_URL`,
  `CRON_SECRET`, `GITHUB_DISPATCH_TOKEN`, and `FIXTURES_DEPLOY_KEY` (the private half of the fixtures deploy key).
- **One secure note holding the Mac's `.env`** (the dev project's values, section 2.6), which also covers `SEC_USER_AGENT`,
  `STORAGE_BUCKET` and `STORAGE_REGION`.
- **Recovery codes** for GitHub (2FA), Supabase, Vercel, the Anthropic Console, MongoDB Atlas, UptimeRobot and Healthchecks.io.

**Not in Bitwarden, on purpose.** These are readable again from the place named, so a copy would only be a second home:

| Value | Where it comes from again |
|---|---|
| Production `SUPABASE_SERVICE_ROLE_KEY` (the secret key) | the production project's dashboard (the API Keys page). `CLAUDE.md` §4 keeps it only in Vercel, so no other copy is kept; if it can't be copied again, create a new key and update Vercel |
| `SUPABASE_ANON_KEY`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (the publishable key, public by design) | the same page |
| `SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `STORAGE_ENDPOINT` | public addresses built from the project reference: the dashboard's Data API page and Storage's S3 connection page |
| `SQL_READER_DATABASE_URL`, `DEV_JOB_DATABASE_URL` | built from the saved role password and the shared pooler host (shown under Connect); the format is in `.env.example`. Dev's URLs are also in the Mac `.env` note |
| `VISITOR_HASH_SALT` | generated by the setup helper and never shown. Nothing reads it yet; replace it with any new random value in Vercel when the command bar is built |
| `STORAGE_BUCKET`, `STORAGE_REGION` | not credentials; shared by dev and production and also in the Mac `.env` note |

**The check** is the owner's: each name in section 2 is in one of the entries above or in the table, and each account has a sign-in
path that doesn't depend on the device that is signed in today. The date of the last check is recorded on issue #199, not here.
