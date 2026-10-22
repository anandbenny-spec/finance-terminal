# Finance Terminal

An AI research terminal for Indian and US markets. The design lives in the claude.ai project docs
("Finance Terminal - Project Brief", "Architecture - 01" to "04"); this repo is the code.

| Path | What |
|---|---|
| `supabase/migrations/` | database schema (Architecture 01–03) |
| `supabase/tests/` | the SQL security and integrity tests; `run_all.sh` builds a throwaway database and runs them |
| `py/` | one Python project (uv, Python 3.13): data jobs, the AI layer, the live API service, evaluations |
| `web/` | the Next.js front end |
| `.github/workflows/` | daily, watch, weekly, refresh, backfill jobs and CI |
| `vercel.json` | one Vercel project, two services: `web` at `/`, `api` at `/api/*` |

## Run the tests

```bash
cd py && uv sync --locked --all-extras
PGHOST=localhost PGPORT=5432 ../supabase/tests/run_all.sh          # needs a throwaway Postgres 15+ with pgvector
TEST_PG_CONNINFO="host=localhost port=5432 dbname=ft" uv run --locked pytest -q
```
