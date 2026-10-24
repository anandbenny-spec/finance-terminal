# web

The Next.js front end (a Vercel service at `/`). It reads published data with the browser key from the `api`
schema only, and calls the Python `api` service under `/api/*` for the command bar and admin actions
(`CLAUDE.md` §2). Layout: Design 01 §7. Created in A0 (Prompt 01, 23 Oct 2026); the design base came in C1.

## Commands

```bash
npm ci               # always from the lock file
npm run lint         # ESLint (eslint-config-next, core web vitals + TypeScript)
npm run typecheck    # next typegen, then tsc --noEmit (the route types LayoutProps needs come from typegen)
npm test             # Vitest, once (tests beside their code, or in tests/ for checks on the package)
npm run build        # next build (Turbopack)
```

CI runs all five on Node 24 (`.github/workflows/ci.yml`, job `web`).

## Versions

- **Node 24** (`engines.node`), **npm 11**. Every direct dependency is pinned to an exact version, and
  `tests/package-pins.test.ts` fails if one gets a range, TanStack Table leaves 8.21, or a library leaves the
  major Design 01 §8.4 names. An upgrade is a named, deliberate change: version, lock and test in one commit.
- Next 16.4.2, React 19.3.1, Tailwind 4.3.3 (wired through `@tailwindcss/turbopack` in `next.config.ts`; there is
  no PostCSS config), TanStack Table **8.21.3** (not v9), Lightweight Charts 5.2.1, supabase-js 2.118.0,
  lucide-react 1.60.0, Vitest 4.1.12 (the 4.1 line; 5.0 is a month old).
- **shadcn 4.22.1**, base **Radix**, preset **Nova** (`components.json`, style `radix-nova`). `cn()` comes from
  shadcn's own `cn` package (a compiled replacement for clsx + tailwind-merge). The `shadcn` package is a dev
  dependency: only the CSS build reads it (`@import "shadcn/tailwind.css"`).
- `next.config.ts` keeps create-next-app 16.4's defaults `cacheComponents` and `partialPrefetching`; how pages cache
  Supabase reads is decided when the first page reads data (C3).

## Design base (C1, 23 Oct 2026)

- `app/globals.css` is Design 01 §2.6 byte for byte (checked against the 21 Oct review pack's copy), plus the two
  `@import` lines `shadcn init` added (`tw-animate-css`, `shadcn/tailwind.css`: variants, keyframes and masks; no
  colours). shadcn's oklch palette, `.dark` block, sidebar roles and radius scale are gone.
- **Two additions after §2.6**, in a marked block at the end; neither changes a colour or a §2.6 value:
  1. `@custom-variant dark (&:is(.dark *))`, as `shadcn init` wrote it, with `dark` on `<html>` (`app/layout.tsx`).
     Without it Tailwind's `dark:` follows the viewer's OS setting, so Nova's primitives would differ between a Mac in
     light mode and one in dark mode. With it, their dark styling always applies (controls take `--input`, which is
     `--line-input`, as their border).
  2. `--radius-lg` … `--radius-4xl` held at 4 px. Nova's primitives use `rounded-lg` and up, which Tailwind's
     defaults make 8–32 px; Design 01 §2.5 allows 2 px and 4 px only.
- `app/fonts.ts` is §2.7 exactly (Plex Sans and Mono, `latin` + `latin-ext`, the subset ₹ lives in).
- `lib/tokens.ts` reads the chart colours from the CSS variables once at mount (Lightweight Charts takes colours in
  JavaScript); `lib/format.ts` formats every number, date and time (Design 01 §3), with every §3 example a test.
- `design/specimen.html` is the visual reference (Prompt 01 §1.1 item 9); its numbers are samples. A test keeps its
  token values equal to `globals.css`'s.
- `NOTICE` carries Lightweight Charts' attribution notice (Apache 2.0); a test ties it to the installed version.
- **Tests that guard the token file** (`tests/globals-css.test.ts`) compile `globals.css` with the installed Tailwind:
  every token at its Design 01 value, every role utility resolving, every radius step 2 or 4 px, and every class in
  `components/ui/` that sets a colour coming from a token (Design 01 §10.3: a colour with no token, Tailwind's own
  palette or a literal colour fails it). Add a primitive with `shadcn add` and the test checks it too.
- Noticed while checking §10.3, left for the components that use them: Nova's button sizes its text with `text-sm`,
  `text-xs` and `text-[0.8rem]` (14, 12 and 12.8 px: nothing below 12 px, but not the role names of §2.4), and shows
  focus as a 3 px `ring-ring/50` instead of the base 2 px amber outline. Both are amber/size choices inside the
  primitive, not colour edits; C2 decides when it first uses a button.

## Shell (C2, 24 Oct 2026)

- `components/shell/`: `AppShell` (the root layout wraps every page in it), `TopBar`, `TickerTape`, `Sidebar` (232 px
  from `xl`, a 56 px rail with tooltips at `lg`, a drawer below `lg`: `MobileNav`), `StatusBar`, `Footer`. Design 01
  §4.1, §5.1 and §5.5; Spec 01 §4.1. `app/not-found.tsx` keeps a missing page inside the shell.
- **Data:** the tape reads `api.series_latest` and `api.series`, the status bar `api.next_meetings` and
  `api.task_freshness`, with the browser key only (`lib/supabase/public.ts`), through `lib/queries/shell.ts` (`queries`,
  not `data`: the root `.gitignore` drops every `data/` folder, so raw data never reaches the repo). Every list
  read goes through `readAll` (`lib/queries/read.ts`): an exact count, then `.range()` pages until every row is in, or
  an error, never a list the Data API's 1,000-row cap cut short (Review 01, N2; A1 brings every page query under it).
- **Caching:** the reads are `'use cache'` with `cacheLife("minutes")` (served up to a minute, then refreshed in the
  background), so the shell is prerendered (`○` in the build) and a decision `watch` stores reaches it within a
  couple of minutes. A failed read is logged on the server and shows the shell's error state; without
  `NEXT_PUBLIC_SUPABASE_*` (CI's build) every read fails that way and the build still passes.
- **Logic is pure and tested** in `lib/shell/`: the tape's items (`tape.ts`), the countdowns and the decision-day
  switch (`meetings.ts`: days count in each bank's own zone, as `formatCountdown` and `ft/jobs/due.py` do), the
  freshness summary (`freshness.ts`) and the sidebar (`nav.ts`). `components/shell/shell.test.tsx` renders the tape
  and the status bar to HTML in every state.
- **Which tasks count** for freshness: `lib/feeds.ts` lists each live part of the site with the tasks that feed it
  (the shell: `policy.market_series`, `policy.calendar`). Each page job adds its panels there when they go live.
- **The countdown is the browser's:** the server sends each bank's next date ("Next FOMC 28 Oct 2026"); after first
  render `Countdowns` adds "· in 5 days" from the visitor's clock (checked a few times a minute) and switches to
  "FOMC decision today, 14:00 ET (23:30 IST)" at midnight in the bank's zone. Cached HTML never carries a stale count.
- **Admin:** the sidebar's admin group and the top bar's badge show when a stored session's `app_metadata.role` is
  `admin` (display only; the API's check is A3's). `lib/supabase/browser.ts` keeps the session under `ft-auth`, so
  visitors with no session never load supabase-js in the browser. C6's login calls `useAdmin().recheck()`.
- `components/ui/tooltip.tsx` and `sheet.tsx` are written by hand on Radix with shadcn's API, because `shadcn add`
  can't reach its registry from Claude's shells. `tests/globals-css.test.ts` now also holds `components/shell`,
  `components/data` and `components/ai` to the tokens, and to CLAUDE.md §5's "no arbitrary values": a size is
  Tailwind's scale or a token variable `globals.css` declares (`h-(--topbar-h)`), never `h-[48px]`.
- **Checking it by eye in Claude's workspace:** `next build` there needs the fonts swapped for `next/font/local`
  with `@fontsource/ibm-plex-*` files, and a local stand-in for the Data API (sample rows, never committed). That's
  how C2's screenshots against the specimen were taken.

## Install scripts

npm 11 runs a package's install script only once it's approved in `allowScripts` (`package.json`). The one script
in the tree, `unrs-resolver`'s postinstall (ESLint's import resolver), is **denied**: it only fetches a native
binding when the platform package is missing, and the Linux and macOS bindings arrive as optional dependencies.
A new package with a script shows in `npm install-scripts ls`; approve or deny it on purpose.

## Working notes for Claude's sessions

- Claude's shells reach npm and GitHub but **not `ui.shadcn.com` or Google Fonts** (checked 23 Oct 2026).
  - `shadcn init` and `shadcn add …` fetch from `ui.shadcn.com`: Anand runs them in macOS Terminal in a scratch
    copy outside the repo (`~/Projects/finance-terminal/setup/shadcn-init/`), and Claude copies the files they write
    into its branch. That's how A0's `shadcn init` ran.
  - `next build` downloads the fonts through `next/font/google`, so it fails in Claude's workspace; CI and Vercel
    build normally. To check the rest of a build locally, build once with the font imports removed and put them back.
- This workspace's npm 10.9 fails on this tree (`Cannot read properties of null (reading 'edgesOut')`, an arborist
  bug with the WebAssembly fallbacks' peers); npm 11 doesn't, and is what Node 24 ships.

## /policy (C3, 24 Oct 2026)

- `app/policy/page.tsx`: Spec 01 §4.2's S rows. Where rates stand (`StatRow`: the Fed range from both bounds in
  `api.policy_meetings` (F22), the RBI repo rate with its stance gated by field policy, the next FOMC and MPC with
  browser-side countdowns), "Policy rates since 2019" (`RatesPanel`), the latest decisions (`PolicyCard` per bank,
  D13) and "Every decision since 2019" (`HistoryPanel`, filters by bank, year and cut / hold / hike).
- **Data:** `lib/queries/overview.ts` reads it all, cached like the shell, each panel on its own (a failed read leaves
  only its panel in the error state). The new reads (`readVotes` in batches of 40 meetings, the decision document, AI
  outputs and citations, field policy, series' source lines) live in `lib/queries/policy.ts` under A1's guard.
- **Logic is pure and tested** in `lib/policy/`: `decisions.ts` (DecisionLine, the tally lines with F23's separate
  rate and stance counts, §4.5's "votes in the minutes" copy, history rows), `rates.ts` (step series expanded to one
  point per day, since Lightweight Charts spaces points by index), `ai.ts` (only cited bullets; quotes cut at a
  sentence; no official link for the RBI).
- **The bands** (Fed range, RBI corridor) are one custom series each (`components/data/band-series.ts`), so Design 01
  §10.2's fallback wasn't needed: checked by eye at 1440 and 375 px against a local stand-in Data API.
- **Links C4 and C5 build:** meeting pages at `/policy/fed/YYYY-MM-DD` and `/policy/rbi/YYYY-MM-DD` (decision date);
  "Open in document" at `/documents/{id}?span={start}-{end}#cited` (code-point offsets, as stored).
- New primitives: `components/ui/popover.tsx` (by hand, like C2's), and the sheet's `bottom` side (a citation on touch).

## Meeting pages (C4, 24 Oct 2026)

- `/policy/fed/[date]` and `/policy/rbi/[date]` (the decision date) render `components/policy/MeetingView.tsx` inside
  Suspense (the params are known only on request), from cached reads in `lib/queries/meeting.ts`. Tabs are links
  (`?tab=changes|statement|minutes|press`), so every tab can be linked; the RBI's Statement tab switches between the
  resolution and the Governor's statement with `&doc=governor`.
- **Text:** a document's text is its stored chunks joined (`lib/policy/text.ts` refuses chunks that don't join), with
  ¶ numbers counted as the differ counts them (blocks separated by a blank line). Offsets are code points throughout.
  `components/ai/DocumentView.tsx` gives every paragraph `#p-N` and can mark a cited span; C5 can reuse it.
- **Redline** (`components/ai/Redline.tsx`, model in `lib/policy/redline.ts`) renders from `api.document_diffs`'
  stored pairs alone, with "Changes only" folding unchanged paragraphs.
- **§4.5 copy** lives in `lib/policy/meeting.ts`. The RBI press-conference copy is proposed (Spec 01's "from February
  2023" no longer holds: #15) and awaits Anand's approval in #21.
- A missing meeting renders the not-found page with HTTP 200, because the page streams before it knows (Suspense).
