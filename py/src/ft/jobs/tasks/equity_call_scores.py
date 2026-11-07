"""equity.call_scores: what changed in each call's opening remarks since the previous quarter's call, as
`equity.call_remarks_change` rows (Spec 02B §5.13, §3.3, §6; D9; job 2B-17, #180). The rules are in
`ft/equity/calls/remarks.py`; the tone part of this task is job 2B-12's (#175) and isn't here yet.

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
- (B) `pairs_capped`: at most 5 pairs per row, each side at most 400 code points;
- (W) `compared`: calls compared, no previous call, no opening text, role-held, and the labels (always passes).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from psycopg.types.json import Jsonb

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
            f.rows[c[0]] = (prev[0], ch.words_base, ch.words_new, ch.words_added, ch.words_removed,
                            Decimal(str(ch.similarity)), ch.new_speakers, ch.label, ch.pairs, rm.RULE_VERSION)
            f.counts["compared"] += 1
            f.counts[f"label_{ch.label or 'none'}"] += 1
        return f

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
            sides = [p[k]["text"] for p in pairs for k in ("old", "new") if p.get(k)]
            if len(pairs) > rm.MAX_PAIRS or any(len(t) > rm.MAX_SIDE for t in sides):
                bad_pairs.append(e)
        c = f.counts
        dist = "; ".join(f"{k.removeprefix('label_')} {n}" for k, n in sorted(c.items()) if k.startswith("label_"))
        return [
            Check("similarity_range", not bad_sim,
                  f"{len(bad_sim)} rows with a similarity outside 0 to 1" if bad_sim
                  else f"all {len(rows)} similarities are between 0 and 1"),
            Check("pairs_capped", not bad_pairs,
                  f"{len(bad_pairs)} rows with more than 5 pairs or a side over 400 characters" if bad_pairs
                  else f"all {len(rows)} rows have at most 5 pairs of at most 400 characters a side"),
            Check("compared", True,
                  f"compared {c['compared']}; no previous call {c['no_previous']}; no opening text {c['no_opening']}; "
                  f"role-held {c['role_held']}; labels: {dist or 'none'}", blocking=False),
            self._distribution(rows),
        ]

    @staticmethod
    def _distribution(rows: list) -> Check:
        """The threshold check of Spec 02B §5.13: deciles of the stored similarities and the share below 0.5 (numbers only).
        Warns when more than 25% fall below 0.5 (Spec 02B §9.4 stops the job there)."""
        sims = sorted(float(s) for _, s, _ in rows)
        if not sims:
            return Check("similarity_distribution", True, "no comparisons yet", blocking=False)
        n = len(sims)
        deciles = [sims[min(n - 1, (n * k) // 10)] for k in range(10)] + [sims[-1]]
        below = sum(1 for s in sims if s < rm.REWRITTEN_BELOW)
        share = below / n
        return Check("similarity_distribution", share <= 0.25,
                     f"n {n}; below 0.5: {below} ({share:.1%}); deciles 0 to 100%: "
                     + ", ".join(f"{d:.3f}" for d in deciles)
                     + ("" if share <= 0.25 else "; MORE THAN 25% BELOW 0.5: stop and show Anand (Spec 02B §9.4)"),
                     blocking=False)
