# #620 scoped rebuild verification

The committed implementation at `13f91ac` passed the isolated integration run on 24 November 2026 with PostgreSQL 17.12, pgvector 0.8.2, PostgREST 16.6, Node 24 and Next.js 16.4.5. The run used real anonymous SQL views and synthetic Calls inputs. It ran from 23:51:30 to 23:52:03 IST. [Request and preparation evidence](VERIFICATION.json)

| Check | Result |
|---|---|
| Default and publication | The anonymous view selected Q1 FY27 while Q2 FY27 was newest on the earnings list. A committed status change selected the already-published Q2 FY27. Publishing Q3 FY27 then queued and replaced the company default, quarter set and earnings list. The unrelated control company's prepared bytes stayed identical across both changes. |
| Withdrawal | Event-only suppression, document-only suppression and field closure were committed independently. Each first group of nine requests returned 503 with no-store and zero quote or figure bytes. This included HTML, quarter Flight, a segment confirmed to contain the quote before closure, the earnings list and a second local hostname. The worker rebuilt each group from the current anonymous data. |
| Unrelated write | A committed update to `ops.job_run` preserved every revision and the checked control response's render ID and SHA-256. |
| Browser | Chrome opened the cited passage in the hydrated page and switched from Q1 to Q2 through a prepared Flight response. The route updated its heading and selected link. The adapter recorded zero Data API reads for the visit and switch. |
| Failed and stale attempts | The worker rejected a 200 page with a failed figure read and retried automatically. A standby worker could not start during an active render. The database refused the old attempt after a committed gate closure. |
| Interruption | The verifier killed the worker with `SIGKILL` while it was inside a render, after committing document suppression. First reads withheld evidence. The replacement began after the old deadline, rebuilt from durable pending revisions and reopened only after the separate restoration commit. |
| Serial capacity and access | Maximum active renders was one. The initial four visible scopes covered seven URLs and 203,982 compressed bytes. The initial render-to-install window was under one second on the fixture. All 1,315 render Data API events correlated to preparation IDs. The visitor login could read the current view and had no access to the underlying artifacts table or manifest writes. `anon` had no schema access. |

The verifier issued 108 recorded requests, including 73 non-cacheable 503s with zero fixture evidence bytes. The fixture kept `ai.call_summary` hidden. The 1,998 web tests and type checking passed. ESLint had zero errors and two existing warnings. The repository SQL suite had identical pass counts and failed checks on the candidate and untouched `main` in this macOS harness. It did not establish a green SQL suite.

Next.js moved from 16.4.2 to 16.4.5 because the older build failed the dependency security check and the patch contains fixes relevant to shared route caches. [Next.js 16.4.5 release](https://github.com/vercel/next.js/releases/tag/v16.4.5)

[Reproduction and production acceptance](README.md) retains the original three rounds of eight, the under-300 ms dev timing check, CI and handoff. This record establishes local behavior on committed fixtures. The protected Vercel candidate, real production roster, promotions, hosted migrations, gate changes and any #185 re-flip remain subject to separate approval.
