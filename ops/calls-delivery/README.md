# Calls response preparation

The visitor-facing Next server serves prepared Calls and earnings responses from PostgreSQL. The reader checks the current manifest on the primary on each HTTP request and verifies the digest of the response bytes. The reader uses a dedicated login that inherits `calls_artifact_reader`. Run the preparation worker separately, using a login that inherits `calls_artifact_worker` and the same immutable Next build, deployment ID and anonymous Data API settings as the visitor application.

| Setting | Use |
|---|---|
| `CALLS_BUILD_ID` | An immutable identifier supplied to `next build`, the visitor server and the preparation process. |
| `CALLS_READER_DATABASE_URL` | Direct or session-pooled primary connection, with TLS certificate verification for a hosted target. |
| `CALLS_WORKER_DATABASE_URL` | Direct or session-pooled primary connection for the advisory lock, queue and manifest writes. |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Existing anonymous Data API settings, set consistently at build and worker runtime. |
| `CALLS_RENDER_TIMEOUT_MS` | Optional hard deadline per operation, 45 seconds by default. The worker uses a separate watchdog and a database expiry five seconds later. |

Run `node ops/calls-delivery/worker.mjs` from `web/` under a service supervisor. The worker polls for outstanding revisions and backs off failed attempts. Its renderer binds a private loopback address and accepts only requests carrying the worker's one-process secret. The `CALLS_PRIVATE_RENDERER` flag is internal to that process. A public server starts without it.

The migration creates separate company and earnings revisions. One company revision covers its default and all published quarter paths, including evidence from its earlier calls. The discovery window displayed by an empty company page depends on the earnings revision. Header changes follow their company, except changes shared across headers, such as index membership. Gate and source visibility changes use a shared revision because their dependencies can include indirect citations, prior calls, shared guidance rates, index results and the shell. An artifact with an obsolete revision is invisible to the reader immediately after commit. While a revision is pending the reader returns 503 with no-store. A valid-shaped quarter absent from a complete current manifest redirects temporarily to the prepared company default.

The worker samples the next clock deadline before reading the anonymous views. Task staleness, results waiting periods and day rollover can change the response without a database write. The reader withdraws expired responses, and the worker rejects an attempt that crosses its deadline and prepares a replacement.

On a protected preview, start capturing deployment logs before making any visitor requests. The first fixture URL should be fetched only after its manifest is ready. Save its completion time, digest, revision, render ID, arrival time and request ID. Keep a separate request to a cold ordinary page as a positive control for `calls-api-outbound`. Match its transport log using the hosting provider's request ID. Look for both visitor-attributed and unattributed outbound requests for each guarded visit, including after response completion. Missing access records or a missing positive control make the hosted result inconclusive.

The worker holds one database advisory lock across each attempt. It renders and validates one path at a time. The worker publishes a company's complete validated set in one transaction, under locks on the current revisions and its render token. If the process disappears, another worker takes over after the previous deadline and resumes the pending revisions. A deployment can have its own worker during staging or rollback. The database still admits one active attempt at a time.

## Repeat the isolated verification

Use a fresh loopback PostgreSQL cluster with pgvector and btree_gist, plus PostgREST exposing only `api` and configured with `db-anon-role=anon`. Create a database named `calls620`. Apply `supabase/tests/mock_supabase_roles.sql`, every migration in order, `supabase/tests/test_fixture.sql` and [fixture.sql](fixture.sql), in that order. The fixture commits its changes. It retains the seeded `ai.call_summary` setting and uses a synthetic quote and figure gated by `ai.guidance_extraction`. After a failed run, use a fresh fixture for another full run.

Run the verifier from `web/` after `npm ci`. This example uses a local trust-authenticated PostgreSQL cluster on 44620 and PostgREST on 44621.

```sh
CALLS_VERIFY_DATABASE_URL=postgresql://postgres@127.0.0.1:44620/calls620 \
CALLS_READER_DATABASE_URL=postgresql://calls_reader_test@127.0.0.1:44620/calls620 \
CALLS_WORKER_DATABASE_URL=postgresql://calls_worker_test@127.0.0.1:44620/calls620 \
CALLS_VERIFY_POSTGREST_URL=http://127.0.0.1:44621 \
CALLS_VERIFY_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
CALLS_VERIFY_EVIDENCE=/tmp/calls620-verification.json \
node ops/calls-delivery/verify.mjs
```

The verifier starts its own Next build, public reader and workers. Its Data API adapter forwards requests to the real PostgREST views as `anon`, records reads and injects a panel failure and a pause for the worker race tests.

## Production acceptance still required

Keep this build off public traffic until the release and production tests are separately approved. Confirm the migration number on [#30](https://github.com/anandbenny-spec/finance-terminal/issues/30), set up the reader and worker logins, and verify that every deployed candidate hostname goes through the guard. Inventory legacy deployments that predate the guard before relying on withdrawal. Verify all 50 published Indian Nifty defaults and each distinct published quarter from the current anonymous roster, including companies with no calls. Complete the readiness check on the same immutable deployment ID used for promotion. A rollback candidate needs prepared responses for the current revisions.

For #620 part 3, keep the three rounds of eight simultaneous first visitor requests to distinct cold default URLs, with 24 predeclared picks and no previous public fetch or prefetch. Use the Mac's built-in browser from `/about` and the issue's same-origin `fetch` with `cache: "no-store"`. The owner decision about counting the first non-preparation visitor request remains part of the release review. Require 8/8 HTTP 200 and zero “Couldn't load” in every round. Inspect matching Vercel warning/error logs within the one-hour window. Require zero statement timeouts for `api.call_figures`, `api.call_guidance`, `api.ai_outputs`, `api.ai_citations`, `api.citation_pages` and `api.remarks_changes`. Report `api.results_moves` separately. Match each response by request ID, deployment, revision, render ID, digest and pre-request completion to the prepared manifest and complete access logs. Any visitor-linked render or Data API read fails the run. After a failed round, use a fresh deployment and three fresh rounds under the issue's stop rule.

Retain the [0009 dev timing check](../../supabase/dev-sql/0009_calls_tab_cold_reads.sql) at under 300 ms per read for at least eight companies, the green CI counts, and the issue handoff. Later #185 testing and any `ai.call_summary` re-flip have their own approval and stop rule. [Issue #620](https://github.com/anandbenny-spec/finance-terminal/issues/620) · [Approved design](https://github.com/anandbenny-spec/finance-terminal/issues/620#issuecomment-7130230554)
