# Finance Terminal

An AI research terminal for Indian and US markets, plus crypto. Every number on it traces back to an official source,
and anything the project can't verify is labelled or left out. It is a student project by Anand Benny. The code is
public so it can be read; the design lives in a set of planning documents that are not part of this repo.

**Informational only, not investment advice.** Nothing here is a recommendation to buy or sell anything.

## Data and ownership

Every piece of data on the site belongs to its owner, and the owner is named on each figure. This is a non-commercial
project: it earns no money, and it sells, licenses or exports no data. That is a promise of no monetary gain and no
data resale, not a claim that nothing is shown, and it does not replace permission. If you own data shown on the site
and want it removed, it is removed.

**No raw source data is in this repo**: no saved exchange or regulator pages, no downloaded data files, no exports. The
jobs fetch from official sources on a low-frequency, cached schedule and keep what they need in the project's own
private storage and database; the browser never calls a data source.

**Licence:** none is granted. The code is published for reading, so all rights are reserved.

## What is in the repo

| Path | What |
|---|---|
| `supabase/migrations/` | the database schema |
| `supabase/tests/` | the SQL security and integrity tests; `run_all.sh` builds a throwaway database and runs them |
| `py/` | one Python project (uv, Python 3.13): data jobs, the AI layer, the live API service, evaluations |
| `web/` | the Next.js front end |
| `.github/workflows/` | the scheduled data jobs, the keep-alive and CI |
| `vercel.json` | one Vercel project, two services: `web` at `/`, `api` at `/api/*` |

## Run the tests

```bash
cd py && uv sync --locked --all-extras
PGHOST=localhost PGPORT=5432 ../supabase/tests/run_all.sh          # needs a throwaway Postgres 15+ with pgvector
TEST_PG_CONNINFO="host=localhost port=5432 dbname=ft" uv run --locked pytest -q
```

Some Python tests read saved source pages from a separate private repository, so they skip without it. In CI, a pull
request from a fork has no access to that repository, so its test job stops at the step that fetches the saved pages.
