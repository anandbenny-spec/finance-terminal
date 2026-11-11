"""equity.call_scores: what changed in each call's opening remarks since the previous quarter's call, as
`equity.call_remarks_change` rows (Spec 02B §5.13, §3.3, §6; D9; job 2B-17, #180). The rules are in
`ft/equity/calls/remarks.py`; the tone part (job 2B-12, #175, `ft/ai/call_tone.py`) is below.

**Tone** (`equity.call_tone` rows, Spec 02B §5.7): for each stored call with a *published* `call_tone` output (the AI run is
`equity.calls_ai` with `CALLS_AI_TASK=call_tone`; this task makes no AI call), the mean, sd and n of its passage scores, the
opening's and the answers' own mean and n, and the change against the previous fiscal quarter's call. A call with fewer than 8
scored passages is stored held ("too little management text to score"), with n and no mean; the change is left empty when the
previous call is missing or held. Same recompute-and-diff as the remarks: new rows inserted, changed ones updated, a row whose
output is gone deleted, a re-run on unchanged data writes nothing. The scores are read from the output's content (no transcript
text); the field policy `ai.call_tone` stays hidden until the tone gate passes and Anand flips it (§5.8).

Database only, no AI and no fetch. For each stored call (event status `stored`, no `status_reason`, a fiscal quarter, a
transcript) the previous call is the same company's call for the immediately preceding fiscal quarter, stored the same way.
No row (counted, never an error) when: there is no previous call; either call has no management opening text; or either call
is role-held (`unknown_role_share` over 0.10, Spec 02B.1 option C: management turns may sit under `unknown`, so a labelling
gap would read as a rewrite; decided by Claude under the standing delegation, 8 Nov 2026).

A run recomputes every row and writes only what changed: new rows inserted, changed ones updated, a row whose call is no
longer comparable deleted (the D table). A re-run on unchanged data writes nothing. Transcript text is read, never logged:
the checks name counts and companies only.

Checks, B blocking, W warning:
- (B) `similarity_range`: every stored similarity is between 0 and 1;
- (B) `pairs_capped`: at most 5 pairs per row, each side at most 400 code points and a verified citation (`remarks-3`, #414:
  `verified` true, a document, a code-point range inside it);
- (W) `compared`: calls compared, no previous call, no opening text, role-held, and the labels (always passes);
- (B) `tone_range`: every stored tone mean is between -2 and +2 with n of at least 8, a held row has no mean, and a change label
  matches its delta and standard error;
- (W) `tone_scored`: calls with a tone, held, with a change label (always passes).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from psycopg.types.json import Jsonb

from ft.ai import call_tone as ct
from ft.equity.calls import remarks as rm
from ft.equity.calls.turns import ROLE_HOLD_SHARE
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register


@dataclass
class Fetched:
    rows: dict[int, tuple] = field(default_factory=dict)       # event_id -> the row to store
    counts: Counter = field(default_factory=Counter)
    scope_ids: list[int] | None = None
    tone: dict[int, tuple] = field(default_factory=dict)       # event_id -> the call_tone row to store


@register
class EquityCallScores(Task):
    key = "equity.call_scores"
    requires = "equity.call_remarks_change"        # the production guard's table (0030)
    scope: frozenset[str] | None = None            # company slugs (tests); None: every company

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (reads only)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        f = Fetched()
        where, args = "", ()
        if self.scope is not None:
            f.scope_ids = [i for (i,) in db.execute("select id from core.entity where slug = any(%s)",
                                                    (sorted(self.scope),)).fetchall()]
            where, args = "and ec.entity_id = any(%s)", (f.scope_ids,)
        calls = db.execute(
            f"""select ec.event_id, ec.entity_id, ec.fiscal_label, ec.transcript_document_id, d.char_count,
                       ec.unknown_role_share
                  from equity.earnings_call ec
                  join core.event ev on ev.id = ec.event_id and ev.status = 'stored'
                  join core.document d on d.id = ec.transcript_document_id
                 where ec.fiscal_label is not null and ec.status_reason is null {where}
                 order by ec.entity_id, ec.period_end""", args).fetchall()
        by_label = {(c[1], c[2]): c for c in calls}
        pairs = []
        for c in calls:
            prev = by_label.get((c[1], rm.previous_label(c[2])))
            if prev is None:
                f.counts["no_previous"] += 1
            else:
                pairs.append((c, prev))
        docs = sorted({x[3] for p in pairs for x in p})
        turns: dict[int, list[rm.TurnRef]] = {}
        if docs:
            for did, a, b, speaker, role, section, w in db.execute(
                    """select document_id, char_start, char_end, speaker, role, section, words from equity.call_turn
                        where document_id = any(%s) and section = 'opening' and role = 'management'
                        order by document_id, turn_no""", (docs,)).fetchall():
                turns.setdefault(did, []).append(rm.TurnRef(a, b, speaker, role, section, w))
        page_rows: dict[int, list[tuple[int, int, int]]] = {}
        if docs:
            for did, pg, a, b in db.execute(
                    "select document_id, pdf_page, char_start, char_end from equity.transcript_page "
                    "where document_id = any(%s) order by document_id, pdf_page", (docs,)).fetchall():
                page_rows.setdefault(did, []).append((pg, a, b))
        cache: dict[int, tuple[str, rm.Opening | None] | None] = {}

        def side(c) -> tuple[str, rm.Opening | None] | None:
            if c[3] not in cache:
                text = db.execute("select string_agg(body, '' order by chunk_no) from core.chunk where document_id = %s",
                                  (c[3],)).fetchone()[0]
                if text is None or len(text) != c[4]:
                    cache[c[3]] = None                         # not whole: nothing to read
                else:
                    cache[c[3]] = (text, rm.opening(text, turns.get(c[3], [])))
            return cache[c[3]]

        for c, prev in pairs:
            held = [x for x in (c, prev) if x[5] is not None and x[5] > Decimal(str(ROLE_HOLD_SHARE))]
            if held:
                f.counts["role_held"] += 1
                continue
            a, b = side(c), side(prev)
            if a is None or b is None or a[1] is None or b[1] is None:
                f.counts["no_opening"] += 1
                continue
            ch = rm.compare(a[0], a[1], b[0], b[1])
            ch.pairs = rm.verify_pairs(ch.pairs, a[0], c[3], page_rows.get(c[3]), b[0], prev[3], page_rows.get(prev[3]))
            f.rows[c[0]] = (prev[0], ch.words_base, ch.words_new, ch.words_added, ch.words_removed,
                            Decimal(str(ch.similarity)), ch.new_speakers, ch.label, ch.pairs, rm.RULE_VERSION)
            f.counts["compared"] += 1
            f.counts[f"label_{ch.label or 'none'}"] += 1
        self._tone(db, f, calls, by_label)
        return f

    @staticmethod
    def _tone(db, f: Fetched, calls: list, by_label: dict) -> None:
        """The tone rows from the published `call_tone` outputs (reads only; the scores carry no transcript text)."""
        docs = [c[3] for c in calls]
        outs: dict[int, tuple[int, dict]] = {}
        if docs:
            for oid, did, content in db.execute(
                    """select id, document_id, content from core.ai_output
                        where task = 'call_tone' and status = 'published' and scope = '' and document_id = any(%s)""",
                    (docs,)).fetchall():
                outs[did] = (oid, content)
        tones: dict[int, tuple[ct.Tone, int]] = {}
        for c in calls:
            got = outs.get(c[3])
            if got is not None:
                tones[c[0]] = (ct.aggregate(got[1].get("scores") or []), got[0])
        for c in calls:
            if c[0] not in tones:
                continue
            tone, out_id = tones[c[0]]
            prev = by_label.get((c[1], rm.previous_label(c[2])))
            pt = tones.get(prev[0]) if prev is not None else None
            ch = ct.change(tone, pt[0] if pt else None, prev[0] if prev is not None else None)
            f.tone[c[0]] = (out_id, tone.n, tone.mean, tone.sd, tone.opening_n, tone.opening_mean, tone.answers_n,
                            tone.answers_mean, ch.prev_event_id, ch.delta, ch.se_delta, ch.label, tone.held_reason,
                            ct.RULE_VERSION)
            f.counts["tone"] += 1
            f.counts["tone_held"] += tone.held
            f.counts["tone_change"] += ch.label is not None

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        scope = "where event_id in (select event_id from equity.earnings_call where entity_id = any(%s))" \
            if f.scope_ids is not None else ""
        args = (f.scope_ids,) if f.scope_ids is not None else ()
        have = {r[0]: r[1:] for r in tx.execute(
            f"""select event_id, base_event_id, words_base, words_new, words_added, words_removed, similarity, new_speakers,
                       label, pairs, rule_version from equity.call_remarks_change {scope}""", args).fetchall()}
        new, changed = [], []
        for eid, row in f.rows.items():
            old = have.get(eid)
            if old is not None and tuple(old) == row:
                continue
            vals = (*row[:8], Jsonb(row[8]), row[9])
            (new if old is None else changed).append((eid, vals))
        stale = [(e,) for e in have if e not in f.rows]
        with tx.cursor() as cur:
            if stale:
                cur.executemany("delete from equity.call_remarks_change where event_id = %s", stale)
            if new:
                cur.executemany("""insert into equity.call_remarks_change (event_id, base_event_id, words_base, words_new,
                                       words_added, words_removed, similarity, new_speakers, label, pairs, rule_version)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                [(e, *v) for e, v in new])
            if changed:
                cur.executemany("""update equity.call_remarks_change set base_event_id = %s, words_base = %s, words_new = %s,
                                       words_added = %s, words_removed = %s, similarity = %s, new_speakers = %s, label = %s,
                                       pairs = %s, rule_version = %s where event_id = %s""",
                                [(*v, e) for e, v in changed])
        self.result.update(inserted=len(new), updated=len(changed), deleted=len(stale))
        return len(new) + len(changed) + len(stale) + self._load_tone(tx, f, scope, args)

    def _load_tone(self, tx, f: Fetched, scope: str, args: tuple) -> int:
        have = {r[0]: r[1:] for r in tx.execute(
            f"""select event_id, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_event_id,
                       delta, se_delta, change, held_reason, rule_version from equity.call_tone {scope}""", args).fetchall()}
        new = [(e, r) for e, r in f.tone.items() if e not in have]
        changed = [(e, r) for e, r in f.tone.items() if e in have and tuple(have[e]) != r]
        stale = [(e,) for e in have if e not in f.tone]
        with tx.cursor() as cur:
            if stale:
                cur.executemany("delete from equity.call_tone where event_id = %s", stale)
            if new:
                cur.executemany("""insert into equity.call_tone (event_id, ai_output_id, n, mean, sd, opening_n, opening_mean,
                                       answers_n, answers_mean, prev_event_id, delta, se_delta, change, held_reason, rule_version)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                [(e, *r) for e, r in new])
            if changed:
                cur.executemany("""update equity.call_tone set ai_output_id = %s, n = %s, mean = %s, sd = %s, opening_n = %s,
                                       opening_mean = %s, answers_n = %s, answers_mean = %s, prev_event_id = %s, delta = %s,
                                       se_delta = %s, change = %s, held_reason = %s, rule_version = %s where event_id = %s""",
                                [(*r, e) for e, r in changed])
        self.result.update(tone_inserted=len(new), tone_updated=len(changed), tone_deleted=len(stale))
        return len(new) + len(changed) + len(stale)

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        scope = "where event_id in (select event_id from equity.earnings_call where entity_id = any(%s))" \
            if f.scope_ids is not None else ""
        args = (f.scope_ids,) if f.scope_ids is not None else ()
        rows = tx.execute(f"select event_id, similarity, pairs from equity.call_remarks_change {scope}", args).fetchall()
        bad_sim = [e for e, s, _ in rows if not (0 <= s <= 1)]
        bad_pairs = []
        for e, _, pairs in rows:
            sides = [p[k] for p in pairs for k in ("old", "new") if p.get(k)]
            if (len(pairs) > rm.MAX_PAIRS
                    or any(len(t["text"]) > rm.MAX_SIDE or t.get("verified") is not True or t["end"] - t["start"] > rm.MAX_SIDE
                           or t["start"] < 0 for t in sides)):
                bad_pairs.append(e)
        c = f.counts
        dist = "; ".join(f"{k.removeprefix('label_')} {n}" for k, n in sorted(c.items()) if k.startswith("label_"))
        return [
            Check("similarity_range", not bad_sim,
                  f"{len(bad_sim)} rows with a similarity outside 0 to 1" if bad_sim
                  else f"all {len(rows)} similarities are between 0 and 1"),
            Check("pairs_capped", not bad_pairs,
                  f"{len(bad_pairs)} rows with more than 5 pairs, a side over 400 characters or a side that is not a verified citation" if bad_pairs
                  else f"all {len(rows)} rows have at most 5 pairs of at most 400 characters a side, each a verified citation"),
            Check("compared", True,
                  f"compared {c['compared']}; no previous call {c['no_previous']}; no opening text {c['no_opening']}; "
                  f"role-held {c['role_held']}; labels: {dist or 'none'}", blocking=False),
            self._distribution(rows),
            *self._tone_checks(tx, scope, args, f),
        ]

    @staticmethod
    def _tone_checks(tx, scope: str, args: tuple, f: Fetched) -> list[Check]:
        rows = tx.execute(f"select event_id, n, mean, sd, delta, se_delta, change, held_reason from equity.call_tone {scope}",
                          args).fetchall()
        bad = []
        for e, n, mean, sd, delta, se, label, held in rows:
            if held is not None:
                ok = mean is None and sd is None and label is None and n < ct.MIN_PASSAGES
            else:
                ok = mean is not None and -2 <= mean <= 2 and n >= ct.MIN_PASSAGES and sd is not None and sd >= 0
                if ok and label is None:
                    ok = delta is None
                elif ok:
                    ok = (delta is not None and se is not None
                          and (label == "more_positive") == (delta > ct.SIGMAS * se)
                          and (label == "more_negative") == (delta < -ct.SIGMAS * se))
            if not ok:
                bad.append(e)
        c = f.counts
        return [
            Check("tone_range", not bad,
                  f"{len(bad)} tone rows out of range, held wrongly or with a change label that doesn't match its delta" if bad
                  else f"all {len(rows)} tone rows are in range, with their change labels matching"),
            Check("tone_scored", True,
                  f"calls with a tone {c['tone']}; held {c['tone_held']}; with a change label {c['tone_change']}", blocking=False),
        ]

    @staticmethod
    def _distribution(rows: list) -> Check:
        """Informational (Spec 02B §5.13 as amended 8 Nov 2026): count, min, median, max and the nine deciles of the stored
        similarities, numbers only. No threshold; it never blocks or fails."""
        sims = sorted(float(s) for _, s, _ in rows)
        if not sims:
            return Check("similarity_distribution", True, "no comparisons yet", blocking=False)
        n = len(sims)
        deciles = [sims[min(n - 1, (n * k) // 10)] for k in range(1, 10)]
        median = sims[n // 2] if n % 2 else (sims[n // 2 - 1] + sims[n // 2]) / 2
        return Check("similarity_distribution", True,
                     f"n {n}; min {sims[0]:.3f}; median {median:.3f}; max {sims[-1]:.3f}; deciles 10 to 90%: "
                     + ", ".join(f"{d:.3f}" for d in deciles),
                     blocking=False)
