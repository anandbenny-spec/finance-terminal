# Two-minute demo script

For a recruiter or interviewer. Open the live site in a desktop browser (1440 px wide) before you start, and say the
one-line pitch first: *"An AI research terminal for Indian markets and the Fed–RBI policy cycle, where every number
shows its source and the AI can only quote what it can cite."*

Read the site as it is on the day: some panels (AI summaries, extracted call figures, model-based valuation fields)
appear only after the owner has checked them, and the site shows a labelled state until then. Each beat has a fallback
line for that.

| # | Time | Click | Say | If the panel shows a held-back or delayed state |
|---|---|---|---|---|
| 1 | 0:00 | Open `/` ("Start here") | "The front page leads with what changed: the latest Fed and RBI decisions and the market tape. Every panel carries its source line and a freshness stamp, and index levels are end-of-day only." | "Update delayed" means a load failed its checks, so the last good data stays and nothing wrong is published. That is the design, not a bug. |
| 2 | 0:15 | Sidebar → **Fed vs RBI** | "One page for both central banks: where rates stand, the latest decisions side by side, and the history. Fed and RBI keep their own colours and their own units." | – |
| 3 | 0:30 | Open the latest meeting (sidebar → **FOMC: what changed**) | "A redline of this statement against the last one, then the key changes. The vote, the minutes and the market reaction sit in tabs. RBI documents are named by date and press-release number rather than linked." | If the AI summary is not showing: "The summary is AI-written and cited, so it stays hidden until it has passed its checks. The redline above it is deterministic and needs no AI." |
| 4 | 0:50 | Click the **AI summary** block's citation chip (if shown) | "Every AI bullet has a citation chip. The quote is rebuilt from the stored document text, and the summary may only contain numbers that appear in the passage it cites. A bullet without a verified citation is dropped, and the database refuses to publish it." | "A missing summary means nothing passed the checks. We prefer a gap to an unchecked claim." |
| 5 | 1:05 | Sidebar → **Screener** | "The Nifty 50 with labelled columns: TTM, derived and proxy figures say so. Each company keeps its own currency and nothing is compared across currencies. The snapshot's date is on the panel." | A column that is not shown is held back by its field policy until its check passes: "hold back the field, not the page". |
| 6 | 1:20 | Open a company → **Valuation** tab | "A reverse DCF: what growth the price implies against what the company has delivered, with every assumption listed with its date and source. No target price, no upside, no buy or sell." | If the model fields are not showing: "The multiples and comparisons against peers are always on. The model outputs stay hidden until a person has looked at them." |
| 7 | 1:35 | Same company → **Earnings calls** tab | "The latest call: a cited summary, and the figures and guidance management stated, each with a quote and a link to its page in the source PDF. Transcript text itself is never shown, only verified quotes of at most 400 characters." | "Summary not generated yet" or "Transcript on the company's website" are neutral states for a call without an AI output or a stored transcript. |
| 8 | 1:50 | Sidebar → **Accuracy** | "The project measures itself: data-quality checks and AI-quality measures, published, including where it falls short. Methodology and limitations are one click away." | An empty measure says why it is empty. |

## Close (2:00)

"Stack: Next.js on Vercel, Python jobs on GitHub Actions, Supabase Postgres with row-level security. The whole
pipeline runs on its own with no Claude subscription, and the AI degrades to labelled states if the key or credit goes."
Point to `README.md` and `docs/OPERATIONS.md` for the architecture and the runbook.

## If asked

- **Where does the data come from?** The About and privacy page lists every source with its source line. Every figure
  names its owner; the project is non-commercial and exports no data.
- **Can the AI make a number up?** It can't publish one: numbers must appear in the cited passage or the returned rows,
  and the database rejects uncited output.
- **Is this advice?** No. Every page says it is informational only.
