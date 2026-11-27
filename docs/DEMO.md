# Two-minute demo script

For a recruiter or interviewer. Open the live site in a desktop browser (1440 px wide) before you start, and say the
one-line pitch first: *"An AI research terminal for Indian and US markets and the Fed–RBI policy cycle, where every
number shows its source and the AI can only quote what it can cite."*

Use the fallback for the state shown on the day.

| # | Time | Click | Say | If the panel shows a held-back or delayed state |
|---|---|---|---|---|
| 1 | 0:00 | Open `/` (**Start here**) | “The tape across the top shows end-of-day index levels. The home page opens with what changed in the latest Fed and RBI statements.” | “Update delayed” means the site keeps showing the last data that passed its checks. |
| 2 | 0:15 | Sidebar → **Fed vs RBI** | “Here are both central banks' policy rates, their latest decisions side by side, and the history. Each bank keeps its own colours and units.” | If an update is delayed, point to the last checked date beside the source. |
| 3 | 0:30 | Sidebar → **FOMC: what changed** | “The cited summary calls out what changed. Scroll down for the sentence-by-sentence redline. The meeting also has tabs for minutes and market reaction.” | If the summary is held back, show the redline. “The redline is computed directly from the two statements.” |
| 4 | 0:50 | Under **What changed**, click the first numbered citation chip | “This opens the passage behind the claim. The quote comes from the stored statement, and every number in a summary bullet must appear in the passage it cites.” | If there are no citation chips, open **Statement**. “The official text is there to read while the summary is unavailable.” |
| 5 | 1:05 | Sidebar → **Screener** (India view) | “The Nifty 50 screener dates its snapshot and labels trailing, derived and proxy figures. Each market has its own currency.” | “A field policy can hide an individual column while the rest of the screener stays usable.” |
| 6 | 1:20 | In the screener, open **HDFC Bank Ltd.** → **Valuation** | “For a bank, the model shows what return on equity the price implies. The assumptions list their dates and sources.” | If delivered return is **Held**, point to its reason. If the model is held back, show the available inputs and their sources. For “Update delayed”, point to the last checked date. |
| 7 | 1:35 | Same company → **Earnings calls**. Scroll to **Guidance given on this call** and **Figures management stated** | “These tables link management’s guidance and figures to pages in the source PDF. The guidance record compares each target with filed results.” | “AI summary held back until it passes review” means review is still required. “Summary not generated yet” means there is no summary to show. For “Transcript on the company's website,” point to **Official source**. |
| 8 | 1:50 | Sidebar → **Accuracy** | “The site publishes data-quality checks and AI evaluation results. The footer links to Methodology and limitations.” | If a measure is empty, read its reason. If the counts are still loading, open **Methodology and limitations**. |

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
