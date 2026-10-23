# Finance Terminal: instructions for every Claude session

An AI research terminal for Indian and US markets, plus crypto, built by Anand Benny as a resume project for
capital-markets recruiters. This file holds only what every session needs. The detail lives in the planning
docs (§8) and in the module's sub-prompt ("Prompt - NN …"), which Anand pastes or names at the start of a session.

**Precedence:** this file and the sub-prompt are the instructions; the planning docs hold the decisions they
point to. If they disagree, or a task seems to need breaking a rule here, stop and ask Anand (§7).
Each session starts by reading this file, its sub-prompt and the Brief (the single source of truth: its header,
plus the sections the sub-prompt names).

## 1. Invariants (never traded for speed)

**Accuracy rule (Brief §1).** Anything a visitor sees as fact (a number, date, label or AI claim) is right,
labelled or absent.
- Correct but derived or approximate: shown with its label (derived, proxy, annualised, monthly, curated,
  back-calculated, AI-extracted). Might be wrong: held back. Hold back the field, not the module.
- **No number the database didn't return.** Pages render figures from `api` views (command-bar answers from
  the guarded `semantic` query), never hard-coded; the specimen's numbers are samples. AI text may hold only
  numbers from its cited passage or the returned rows (`numbers_grounded` also allows the row count, a literal
  in the guarded query and tenors such as "10-year"). The command bar never answers from general knowledge.
- **No uncited AI claim.** A summary bullet without a verified citation into our stored text is dropped,
  and the database refuses to publish an uncited output (Arch 01 §1.4). Never work around either check.
- A load whose blocking checks fail rolls back: the last good data stays, the health page flags it, and
  visitors see "Update delayed". Bad numbers are never published.
- Speed wins on process only: polish is "correct and clear"; modules are sequenced, never cut.

**No personal portfolio** or holdings anywhere. Removed features stay removed (Brief §8, "Removed").

**Currency guard (Brief §4.4, §6.3).** Every company record carries `market` and `currency`. Figures stay
in their reporting currency; nothing compares, ranks or sums across currencies. Comps stay within one market;
DCF inputs come from the company's own market. Cross-market answers show each company in its own currency,
or use currency-neutral ratios (margins, growth, multiples).

**NSE posture (Brief §6.2).** A non-commercial student project. Every NSE-derived figure says "Source: NSE".
No raw-data export of any kind (no download, API, repo file or release asset). Low-frequency, cached jobs only
(weekly; daily only while an IPO is open). The browser never calls NSE, or any data source: it talks only to
the site and Supabase. Anything NSE objects to is removed at once (`core.source.is_suppressed`), with BSE as
the fallback. NSE tasks are never visitor-refreshable.

**Sources, copyright and ownership (Brief §6.2, §10, §11).**
- Every figure shows its source line (`SourceLine`, from `core.source` or the series' attribution) and its
  timestamp (`Freshness`). Index levels are end-of-day only.
- The four US index series also carry a copyright line: S&P Dow Jones Indices LLC (S&P 500, Dow) and
  Nasdaq, Inc. (Nasdaq Composite, Nasdaq-100).
- **Data ownership statement** (decided by Anand, 22 Oct 2026), opening the "About and privacy" note: every
  piece of data belongs to its owner, named on each figure, and the project is non-commercial: it earns no
  money, and sells, licenses or exports no data. It promises no monetary gain and no data resale, not that
  nothing is shown, and it doesn't replace permission: anything an owner objects to is still removed.
- "Informational only, not investment advice" on every page. Valuations and rankings are informational, never
  buy/sell recommendations, verdicts or upside percentages (Brief §11).
- Other sources' terms (CoinGecko, Coin Metrics, SEBI, transcripts, Yahoo) are in Brief §6.2: read the entry
  before touching that source.

## 2. Repo layout and commands

```
finance-terminal/            on the Mac: ~/Projects/finance-terminal/app
├── supabase/migrations/     numbered, timestamped files: 20261022001100_review_01.sql is 0011
├── supabase/tests/          run_all.sh: fresh database, every migration, fixture, every test script
├── supabase/config.toml     Data API: schemas = ["api"], max_rows = 1000; sign-ups off
├── py/                      one uv project, Python 3.13 (>=3.13,<3.14); uv.lock covers the Mac and Linux
│   ├── src/ft/storage.py    the one storage function
│   ├── src/ft/ai/           sqlguard, citations, client (the model wrapper: Claude and Voyage), summaries, extraction
│   ├── src/ft/jobs/         run.py (CLI), config, due rules, runner, watch loop, fetch.py; tasks/
│   ├── src/ft/engines/      document/ (canonical text, chunker, differ, store); time series, events (to come)
│   ├── src/ft/policy/       module 1's source parsers and rules: dates, fed_calendar, rbi_calendar, meetings,
│   │                        fed_statement, fed_minutes, fed_transcript, rbi_resolution, members (the rosters)
│   ├── src/ft/api/app.py    FastAPI service on Vercel at /api/* (entrypoint py/main.py, main:app)
│   ├── config/              tasks.yaml · prompts/<task>/vN.md + schema · ai_prices.yaml (dated) · eval/sets/ ·
│   │                        curated YAML (meeting_times, members)
│   └── tests/               pytest
├── web/                     Next.js, created in the slice (layout: Design 01 §7)
├── .github/workflows/       daily · watch · weekly · refresh · backfill · ci
└── vercel.json              one project, two services: web at /, api at /api/*
```

```bash
cd py && uv sync --locked --all-extras                     # always --locked; the lock changes only on purpose
uv run --locked ruff check src tests
PGHOST=localhost PGPORT=5432 ../supabase/tests/run_all.sh  # throwaway Postgres 15+ with pgvector 0.7+, trust auth
TEST_PG_CONNINFO="host=localhost port=5432 dbname=ft" uv run --locked pytest -q
uv run --locked python -m ft.jobs.run --workflow daily     # what the workflows call (watch, weekly, refresh, backfill)
cd web && npm ci && npm run lint && npm run build          # plus the typecheck and unit-test scripts, once web/ exists
```

- **Tests pass before anything is called done:** ruff; `run_all.sh` with every script at "failed 0"; pytest
  with `TEST_PG_CONNINFO` set (database tests skip without it, and a skip isn't a pass); the `web/` checks.
  Counts only grow, and CI enforces it against main: `test` fails if the SQL or pytest pass count is below
  main's (`ft/ci_counts.py`), unless Anand adds `counts-drop-approved` (Claude never does). No count lives in
  this file, so parallel pull requests don't collide. Say where they ran: CI on the pushed commit counts (§6).
- New behaviour gets a test; a fixed bug gets a regression test.
- Deleting, skipping or weakening a test, or loosening a data check, is Anand's call (§7).
- A new dependency or `uv lock --upgrade` is a named, deliberate change. The API service's base dependencies
  stay small (Vercel installs only those; fetch and parse libraries go in the `jobs` extra).
- The frontend reads published data straight from `api` views with the browser key; it calls `/api/*` only
  for the command bar and admin actions. All AI and security code is Python, written once (Arch 04 §1.2).

## 3. Schema rules

- **The core is frozen** (20 Oct 2026). Any change is a **new migration**, with its reason recorded in the
  planning doc that adds it. Never edit an applied migration.
- **Numbers are sequential:** the next is one past the highest in `supabase/migrations/`. **0012 is module 1's
  seeds** (Spec 01 §1.3: rows and comments, no structural change). Each migration ships with tests that
  `run_all.sh` runs.
- New tables: RLS on, explicit policies, explicit grants (never rely on defaults). New `api` views:
  `security_invoker = true`. New `semantic` views: owned by `semantic_owner`, `security_barrier = true`, with
  `COMMENT`s. The comments are the text-to-SQL prompt's schema description, so keep them true.
- **Field policy fails closed:** no `core.field_policy` row means hidden. A held-back field is shown only once
  the reason it was held back is resolved (for an AI score, when its evaluation gate passes: Brief §11).
- **Store once, never lose:** jobs can't delete source data; changed values go through the revision log;
  published AI outputs are frozen (superseded or rejected, never edited); one published output per task,
  target and scope; a document's text is fixed once chunked (new text is a new version).
- `numeric` for figures, `timestamptz` for instants, `date` for dates, text with a CHECK instead of enums.
  Citation offsets count Unicode code points; JavaScript counts UTF-16, so slice on the server or by code point.
- Every series stores its fixing time and IANA zone. Every observation is stored once fetched; a backfill never
  depends on re-fetching.

## 4. Security boundaries (Arch 01 §1.9–§1.10, Arch 02 §1.6–§1.7, Arch 03 §1.7)

- **The browser key is public by design** (the anon key, or Supabase's newer publishable key). So the Data
  API exposes the `api` schema only, with GraphQL off; **RLS is on for every table**; sign-ups are off and the
  single admin is created by hand.
- **The service-role key** (or the newer secret key) **lives only in Vercel**, used server-side by `py/`.
  Never in GitHub, the browser, a `NEXT_PUBLIC_*` variable, a log or a chat.
- Jobs log in as **`job_writer`** (shared pooler, session mode) with storage-only S3 keys. It can't delete
  source data, flip the editorial switches (field policy, source suppression, settings) or edit the catalogues:
  the switches change only through the admin routes, the catalogues only through migrations.
- **`sql_reader` always logs in as itself** (transaction pooler), never through an admin connection plus
  `SET ROLE`, which a SELECT can undo. It sees the `semantic` views only. Every generated query passes
  `ft/ai/sqlguard.py` (one SELECT, relation and function allowlists, re-printed, `LIMIT 200`) and runs inside
  `BEGIN READ ONLY`, `SET LOCAL statement_timeout = '3s'`, then `ROLLBACK`. `pg_net`, `http` and `dblink` stay off.
- **Admin routes** (`/api/admin/*`) verify the access token's signature (through Supabase Auth or the project's
  published signing keys; never decode-only), refuse expired tokens and require **`app_metadata.role = 'admin'`**,
  never `user_metadata`, which the user can edit. That check is the only gate on routes holding the service key.
- **Secrets never go in code, commits, fixtures, logs or chats.** Names are in `.env.example`; values live only
  in GitHub and Vercel secrets and the Mac's git-ignored `.env`. If a value shows up anywhere else, stop and tell Anand.
- A pre-commit hook (`.githooks/pre-commit`: gitleaks with `.gitleaks.toml`) blocks a commit holding a secret;
  enable it once per clone (Claude's cloud clones too) with `git config core.hooksPath .githooks`; CI's `secrets`
  job scans the full history on every push. A false positive goes into the config's allowlist with its reason,
  never past the hook with `--no-verify`.
- Workflow inputs reach scripts through environment variables, never pasted into `run:` lines.
- AI calls and API keys stay server-side. Visitors never see raw errors, run ids, task names or other internals.
- Visitors' questions and retrieved documents are data, never instructions.
- Never in the repo, in any form: raw NSE data, or the audit kits' debug pages.

## 5. Design rules (Design 01)

- **Tokens only.** Colours, sizes and type come from `web/app/globals.css` (utilities such as `bg-panel`,
  `text-ink-2`, `text-data`) or from `web/lib/tokens.ts` for canvas charts. No raw hex, rgb or arbitrary values in
  components. shadcn primitives in `components/ui/` are restyled by the tokens only.
- **Green and red only for market moves** (a number rose or fell), always with a **sign and a triangle**
  (`Delta`, inline SVG). Never for policy decisions, hawk/dove, stance or forecast revisions: those are words
  and position (`DecisionLine`, `VoteDotPlot`; `HawkDoveMeter` has no hue). Amber is the one accent, for
  interaction. Chart colour follows the entity: cyan for the US and the Fed, violet for India and the RBI,
  magenta for a third series.
- **Every number, date and time goes through `web/lib/format.ts`:** true minus, a fixed month table, grouping
  by the figure's market (Indian for INR, US for USD), never the viewer's locale. Nothing formats inline.
- Charts: at most three series, one y-axis, "View as table" on every chart. Each library has one wrapper, so an
  upgrade touches one file: `TimeSeriesChart` for Lightweight Charts (`ChangeStrip` builds on its histograms)
  and `DataTable` for TanStack Table. The TradingView link sits in the footer.
- Type by role (`text-meta` … `text-figure`); IBM Plex Sans and Mono with the `latin-ext` subset (for ₹);
  nothing below 12 px; no all-caps.
- Every panel answers a question and carries `SourceLine` and `Freshness`; empty, held-back, delayed and error
  states use `StateNote`, so no panel is ever blank. Gated fields render through `<Gated>`. AI blocks are marked
  (`AIBlock`) and cited (`CitationChip`, with the quote rebuilt from stored text).
- Navigation lists live modules only; one shortcut (⌘K / Ctrl K). WCAG 2.2 AA; no sideways page scroll at 320 px.
- Build from Design 01 §4's components, with `web/design/specimen.html` as the visual reference. A new visual
  pattern is a question for Anand.

## 6. Working rules

- **Check installed versions before writing against an API.** TanStack Table is pinned at **v8.21**: v9 came
  out after your training, so don't upgrade it. Next 16, React 19, Tailwind 4, shadcn 4 (`--base radix`),
  Lightweight Charts 5, lucide-react 1.x and some locked Python packages may postdate what you know too: read
  the installed package's typings or docs first. Versions: Design 01 §8.4 and Arch 04a.
- **The Data API returns at most 1,000 rows per request and truncates without an error.** Never read a long
  daily series whole from `api.observations`: step series come from `api.observation_steps`, latest values from
  `api.series_latest`, and long daily series are paged or thinned. List queries check the rows returned against
  an exact count.
- **Every AI call goes through the one wrapper** (`ft/ai/client.py`). Haiku 5.5 first; Sonnet 5.5 only where an
  evaluation shows Haiku falls short, plus text-to-SQL's one retry after a guard rejection or database error
  (Brief §4.3). No `temperature`, `top_p` or `top_k`; no assistant prefill (JSON comes
  through structured outputs). Keep each request under 100,000 tokens; never resend an unchanged input
  (`ops.ai_request`); the $15 monthly switch (`ai_monthly_budget_usd`) stays on.
- **Every raw file goes through `ft/storage.py`**, and every fetch through the shared HTTP client (pacing,
  retries honouring `Retry-After`, conditional requests; Arch 02 §1.5).
- **One module per session.** A module may take several sessions; start each fresh from this file, the
  sub-prompt and the Brief, not from a long thread. **Read only what the job needs (Anand, 23 Oct 2026):** where
  a job's issue names the doc sections to read, read those instead of whole docs (this replaces a sub-prompt's
  "read … in full"); where it names none, follow the sub-prompt's reading list.
- **Fresh sessions at break points (Anand, 23 Oct 2026; it saves his plan's usage).** When a sub-prompt job's
  done-when check passes, or the thread grows long, write a short handoff in that job's GitHub issue (done, next,
  open questions, files touched), then tell Anand it's a good point to start a fresh session from this file,
  the sub-prompt and that issue. **The next session's opening prompt is `/next-job`** (Anand, 24 Oct 2026: his
  skill that takes the next job in tracker #30's order), plus anything he must do first. Write a full prompt only
  when a job needs instructions its issue doesn't hold.
- **CI runs the full suite (Anand, 23 Oct 2026).** In a session, run the tests the change touches; `ci.yml`
  runs ruff, every SQL script and pytest on each push (and on `workflow_dispatch`, which Claude may start).
  Read the result from the run's annotations: each failing test and the counts are annotated, and Claude's
  workspace can't download logs. "Done" means CI green on the pushed commit, with the counts stated.
- **Write files to the Mac or the repo before the session ends.** A session's cloud workspace is invisible to
  every other session. After writing a file to the Mac, read it back there.
- **Branches and pull requests (Anand, 23 Oct 2026; replaces "Anand commits and pushes").** Claude commits in
  its own cloud clone, pushes only to `claude/<job>` branches it created, and opens a pull request; CI runs on
  it. If `main` moves, merge `main` into the branch; never rebase a branch that's been pushed (Anand, 24 Oct 2026).
  **Claude merges its own pull requests (Anand, 23 Oct 2026, 22:16 IST, so the build never waits on him):**
  once every check on the pull request's latest commit has passed (`test`, `web`, `secrets`) and the handoff
  comment is written, Claude merges it into `main` with a merge commit through GitHub's merge API. A pull request
  holding anything §7 or the sub-prompt's stop conditions name waits for Anand, and so does one he's asked to see
  first. A merged migration still needs his `supabase db push` (say so). Anand reviews merged work when he
  chooses and reverts what he doesn't want. Claude never pushes to `main` directly, merges a pull request that
  isn't its own or whose checks failed or are still running, force-pushes or rewrites `main`'s history. On the
  Mac (Anand's clone) Claude still runs no git command that writes, `git status` included; read-only `git log`,
  `git show` and `git --no-optional-locks diff` are fine.
- **Flag a conflict with a decided rule instead of changing it.** Decisions are dated in the docs, and Anand
  reverses them. Build progress lives in git and GitHub issues, never in this file or the planning docs.
- **Standing delegation (Anand, 24 Oct 2026).** Where the docs are silent and a choice only tightens a rule or
  check, or picks between options that keep every rule, Claude decides, records it on the job's issue as "Decided
  by Claude (standing delegation, 24 Oct 2026)" with the reason and the alternative, and carries on. Anand reviews
  these when he chooses and reverses what he doesn't want. Everything in §7 and the sub-prompt's stop conditions
  still stops for him.
- **Where things run (checked 22 Oct 2026):** Claude's shells (the session's cloud workspace and the Mac's Linux
  VM) reach GitHub, PyPI, npm and the Anthropic API, but not the data sources or Supabase. So unit and SQL tests
  run in Claude's workspace (on a local Postgres with pgvector) or in CI. Anything that touches a live source or
  the real project runs on GitHub's runners or in Anand's Terminal on the Mac, and Claude reads the results from
  the run status, the Mac folder or what Anand pastes. Anand starts workflow runs (they cost Actions minutes and
  API credits), with one standing exception (Anand, 22 Oct 2026): Claude may open, update and close GitHub
  issues, and start `ci` and manual check workflows against the **dev** project. Claude never pushes to `main`,
  and never starts production runs, backfills or anything that spends AI credits without asking.
- The Mac's shell can't run macOS virtual environments, and deleting files there needs Anand's permission.
- **Campus Wi-Fi (Sophos, from 26 Oct):** `UV_SYSTEM_CERTS=true` for uv, `NODE_EXTRA_CA_CERTS` for npm and the
  Vercel CLI, `http.sslCAInfo` for git. Production (GitHub, Vercel) is unaffected.
- **End each piece of work with:** the files changed, the tests run and where (the CI run on the pull request),
  the pull request's link (and its merge), and anything Anand must do (a secret, a dashboard setting, a
  `supabase db push`, a run to start).

## 7. Stop and ask Anand before you

- loosen, skip or delete a check or a test (once he agrees, write the reason next to the check);
- change the core schema or an applied migration, or add a migration the sub-prompt doesn't name;
- flip a field policy, a source suppression, a held-back gate or a runtime setting;
- publish anything the accuracy rule would hold back, or add a data source the docs don't list;
- take a fallback the docs don't already name, or act against a dated decision;
- spend beyond the plan: an AI run estimated above $5, or any paid plan or add-on;
- do anything irreversible on a live service: delete data or stored objects, rotate keys, change roles.

## 8. Where the detail lives

The planning docs are in the claude.ai project "Finance Terminal". Read them with the Projects tool
(`project_read`, `project_search`). If the project isn't attached, ask Anand to attach it or paste the section;
never rebuild a rule from memory.

| Short name | Project doc | For |
|---|---|---|
| Brief | Finance Terminal - Project Brief | scope, stages and dates, sources, data rules, compliance |
| Arch 01 / 01a | Architecture - 01 Database schema · 01a Schema SQL and tests | schemas, roles, RLS, views, size budget |
| Arch 02 / 02a | Architecture - 02 Refresh jobs · 02a Job runner prototype | workflows, task contract, checks, refresh button |
| Arch 03 / 03a | Architecture - 03 AI layer · 03a AI layer prototypes | wrapper, citations, guard, retrieval, evaluation |
| Arch 04 / 04a | Architecture - 04 Storage, repo layout, Python and lock file · 04a Repo skeleton | storage, CI, one-time setup, secrets |
| Design 01 | Design - 01 Design system | tokens, formatting, components, layouts |
| Spec NN | Spec - 01 Fed vs RBI policy tracker (one per module) | inputs, rules, outputs, UI, done when |
| Prompt NN | Prompt - 01 Fed vs RBI sub-prompt (one per module) | the module's build order and stop conditions |
| Review 01 | Review - 01 Argon external review triage | the 22 Oct fixes and why |
| Data Audit NN | Data Audit - 00 Process and status · 01 … 07 | source evidence; closed audit docs are frozen records |

On the Mac, `~/Projects/finance-terminal/` holds `app/` (this repo), `data-audit/` (the audit kits: read-only
reference code, ported rather than imported) and `review-packs/`.
