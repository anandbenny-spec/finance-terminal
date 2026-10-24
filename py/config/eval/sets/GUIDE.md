# Scoring guide: Pass 2's labelled sets

For Anand, labelling on `/admin/labelling` (Prompt 01 E1; Architecture 03 §1.9). One page; read it once before the
first item. Label from the text on the page alone: what the document says, not what came after it.

## Hawk/dove (`policy_hawk_dove`, `policy_member_hawk_dove`)

**The question:** how hawkish or dovish is this text, as a signal about the bank's policy rate? A whole number from
−2 to +2. Dovish means leaning towards lower rates or keeping support in place; hawkish, towards higher rates or
removing support. Read the decision (highlighted), then the reasoning and guidance below it.

| Score | Means | Typical signs |
|---|---|---|
| **−2** | strongly dovish | a large or emergency cut; support "as long as necessary"; growth risks dominate |
| **−1** | mildly dovish | a routine cut, or a hold that signals cuts ahead; downside risks stressed |
| **0** | neutral | a hold with two-sided wording; neither direction signalled |
| **+1** | mildly hawkish | a routine hike, or a hold that signals hikes ahead; inflation risks stressed |
| **+2** | strongly hawkish | a large or off-cycle hike; "strongly committed" to the inflation target; more hikes signalled |

**Anchors** (one per score; they're never items). Chosen by Claude; the decisions and the quoted phrases are from
Claude's own knowledge, not read from our stored text, so open each meeting page and check it against the document
before relying on it, and move an anchor if you read it differently:

| Score | Fed (FOMC statement) | RBI (MPC resolution) |
|---|---|---|
| −2 | 15 Mar 2020: unscheduled cut of 100 bps to 0–¼%, large asset purchases, rates held there until the economy "has weathered recent events" | 27 Mar 2020: off-cycle cut of 75 bps; accommodative "as long as necessary" to revive growth and fight COVID-19's impact |
| −1 | 31 Jul 2019: a 25 bps cut; the Committee "will act as appropriate to sustain the expansion" | 7 Feb 2025: a 25 bps cut, stance kept neutral |
| 0 | 29 Jan 2025: a hold after the 2024 cuts, with wording that leans neither way | 6 Aug 2025: a hold, stance neutral |
| +1 | 16 Mar 2022: the first hike, 25 bps; "ongoing increases" expected | 8 Feb 2023: a 25 bps hike, withdrawal of accommodation, a 4–2 vote |
| +2 | 15 Jun 2022: a 75 bps hike; "strongly committed to returning inflation to its 2 percent objective" | 4 May 2022: off-cycle hike of 40 bps; focus on withdrawing accommodation |

**Rules of thumb**
- The size of a move sets the starting point; the guidance moves it at most one step. A cut that warns against
  expecting more ("a mid-cycle adjustment") can be −1 rather than −2; a hold that pre-announces hikes can be +1.
- Score the text, not the outcome you know.
- **Member statements** (`policy_member_hawk_dove`): score the member's own view, read against the decision. A member
  who votes with the majority but argues for vigilance on inflation is more hawkish than one who argues for support;
  a member who votes for a different move is scored by that preference.
- The reason line: one short sentence naming what decided the score ("75 bps hike, more to come").

## Stance (`policy_stance`)

**Fed items: guidance direction**, labelled blind, one of three. The question: which way does the statement's
forward guidance point for the next move?

| Label | Means | Anchor (never an item) |
|---|---|---|
| **easing bias** | the next move signalled is a cut, or further cuts | 18 Sep 2024: "in considering additional adjustments to the target range" after a 50 bps cut |
| **neutral** | no direction signalled | 30 Jan 2019: the Committee "will be patient as it determines what future adjustments" may be appropriate |
| **tightening bias** | the next move signalled is a hike, or further hikes | 26 Jul 2023: "in determining the extent of additional policy firming that may be appropriate" |

**RBI items: the stated stance**, from the closed list (Spec 01 §3.3): `calibrated tightening` · `neutral` ·
`accommodative` · `accommodative, focused on withdrawal of accommodation` · `withdrawal of accommodation` (or
`other`, with the reason). It's factual: the MPC states it in the resolution. Claude has drafted these; the page
asks for your label first and shows the draft only after you save yours, so a disagreement is flagged, not anchored.

## Saving

Labels stay in this browser as you go. **Export** downloads one JSON file per set; commit each as
`py/config/eval/sets/<set>.labels.json`. That commit's SHA becomes `ops.eval_run.set_version`. Claude then labels the
same items independently as the second rater, without seeing yours; you settle the disagreements, and the agreement
rate is reported with the gate.
