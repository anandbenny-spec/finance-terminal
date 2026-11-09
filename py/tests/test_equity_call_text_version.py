"""equity.call_text and the analysis version (job 2B-09b, #341): which stored PDFs a run reads, and what it writes.

These tests need no database and no saved pages: `fetch` and `load` run against a fake connection and a fake fetcher that
counts the reads (the end-to-end runs on the test database are in test_equity_call_text_task.py)."""
import itertools
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from ft.engines.document.chunk import Chunk
from ft.equity.calls import pdfs, turns
from ft.jobs.tasks import equity_call_text as ect

SLICES = 4
SHA = bytes(range(32))
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)                  # a Sunday, the weekly workflow's day
THIS = ect.analysis_version()


def at(weeks: int) -> datetime:
    return NOW + timedelta(weeks=weeks)


def slice_now(now: datetime = NOW) -> int:
    return ect.week_index(now.date()) % SLICES


def an_id(in_this_slice: bool, now: datetime = NOW, after: int = 0) -> int:
    """An event id that is (or isn't) in the integrity slice of the run at `now`."""
    return next(i for i in itertools.count(after + 1) if (ect.slice_of(i, SLICES) == slice_now(now)) == in_this_slice)


# ------------------------------------------------------------------------------------------------ the version string
def test_the_same_inputs_give_the_same_version_and_any_change_gives_another():
    parts = [("a", b"alpha"), ("b", b"beta")]
    base = ect.version_of(parts, "calls-1", "6.0")
    assert base == ect.version_of(list(parts), "calls-1", "6.0") and len(base) == ect.VERSION_LENGTH
    assert ect.version_of([("a", b"alphb"), ("b", b"beta")], "calls-1", "6.0") != base          # one byte of a source
    assert ect.version_of([("a", b"alpha"), ("b", b"bet")], "calls-1", "6.0") != base
    assert ect.version_of([("b", b"beta"), ("a", b"alpha")], "calls-1", "6.0") != base          # the order of the files
    assert ect.version_of([("ab", b"alpha"), ("", b"beta")], "calls-1", "6.0") != base          # where one file ends
    assert ect.version_of(parts, "calls-2", "6.0") != base                                      # the parser's version
    assert ect.version_of(parts, "calls-1", "6.1") != base                                      # the PDF library's version


def test_the_real_version_is_stable_and_every_versioned_source_changes_it(monkeypatch):
    assert ect.analysis_version() == THIS == ect.analysis_version()
    assert set(ect.VERSIONED_MODULES) >= {"ft.equity.calls.pdfs", "ft.equity.calls.turns", "ft.engines.document.chunk"}
    real = ect._source
    for name in ect.VERSIONED_MODULES:
        def changed(module_name, name=name):
            n, data = real(module_name)
            return (n, data + b"\n# a new rule") if n == name else (n, data)
        monkeypatch.setattr(ect, "_source", changed)
        assert ect.analysis_version() != THIS, name
    monkeypatch.setattr(ect, "_source", real)
    monkeypatch.setattr(pdfs, "PARSER_VERSION", pdfs.PARSER_VERSION + "-next")
    assert ect.analysis_version() != THIS


def test_the_version_does_not_depend_on_the_slice_size_or_on_this_module(monkeypatch):
    # the size of the slice is a setting (calls.yaml) and this module holds orchestration: changing either must not make
    # every call re-read
    assert ect.load_calls_config().call_text_integrity_slices == SLICES
    assert ect.__name__ not in ect.VERSIONED_MODULES
    monkeypatch.setattr(ect, "load_calls_config", lambda: SimpleNamespace(call_text_integrity_slices=8))
    assert ect.analysis_version() == THIS


# ------------------------------------------------------------------------------------------------ the rotating slice
def test_week_index_counts_mondays_without_a_break_across_new_year():
    mon = date(2026, 12, 28)                                      # Monday of ISO week 53 of 2026
    days = [mon + timedelta(days=d) for d in (0, 6, 7, 13, 14)]   # Mon, Sun, Mon of ISO week 1 of 2027, Sun, Mon
    assert [d.isocalendar().week for d in days] == [53, 53, 1, 1, 2]
    w = [ect.week_index(d) for d in days]
    assert w == [w[0], w[0], w[0] + 1, w[0] + 1, w[0] + 2]
    assert all(ect.week_index(date(2026, 11, 9) + timedelta(days=d)) == ect.week_index(date(2026, 11, 9)) for d in range(7))


def test_over_four_consecutive_weekly_runs_every_call_is_in_the_slice_exactly_once():
    ids = range(1, 401)
    for start in (date(2026, 11, 8), date(2026, 12, 20), date(2027, 1, 3), date(2027, 12, 26)):    # incl. year ends
        days = [start + timedelta(weeks=k) for k in range(SLICES)]
        for i in ids:
            assert sum(ect.in_slice(i, d, SLICES) for d in days) == 1, (i, start)
        for d in days:                                                 # a run reads about a quarter, never most
            share = sum(ect.in_slice(i, d, SLICES) for i in ids) / len(ids)
            assert 0.15 < share < 0.35, (d, share)


def test_a_call_stays_in_the_same_slice_all_week_whatever_the_hour():
    monday = date(2026, 11, 9)
    for i in (an_id(True), an_id(False), an_id(True, after=50), an_id(False, after=50)):
        week = {ect.in_slice(i, monday + timedelta(days=d), SLICES) for d in range(7)}
        assert len(week) == 1, i                                                     # Monday to Sunday: one answer


def test_read_reason():
    kw = {"current": "v2", "day": NOW.date(), "slices": SLICES}
    out, inside = an_id(False), an_id(True)
    assert ect.read_reason(chunked=True, stored="v2", event_id=out, **kw) is None                  # (a) skipped
    assert ect.read_reason(chunked=True, stored="v2", event_id=inside, **kw) == "slice"
    assert ect.read_reason(chunked=True, stored=None, event_id=out, **kw) == "version"            # (b) no version yet
    assert ect.read_reason(chunked=True, stored="v1", event_id=out, **kw) == "version"            # (c) the rules changed
    assert ect.read_reason(chunked=False, stored=None, event_id=out, **kw) == "new"               # (d) a new call
    assert ect.read_reason(chunked=False, stored="v2", event_id=out, **kw) == "new"


# ------------------------------------------------------------------------------------------------ fetch and load, faked
class Cur:
    def __init__(self, rows=(), rowcount=0):
        self.rows, self.rowcount = list(rows), rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    """The queries `fetch` makes. `calls`: {event_id: dict(doc, version, chunked)}; every chunked call stores the turns and
    chunks the patched rules below derive, so it counts as unchanged."""

    def __init__(self, calls, has_column=True):
        self.calls, self.has_column = calls, has_column

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "from pg_attribute" in s:
            return Cur([(1,)] if self.has_column else [])
        if "from equity.earnings_call ec" in s:
            assert ("null::text" in s) == (not self.has_column)
            return Cur((eid, f"zf-{eid}", None, c["doc"], c["doc"] + 1000, c.get("sha", SHA),
                        datetime(2026, 11, 1, tzinfo=UTC) if c["chunked"] else None, True, c["version"])
                       for eid, c in sorted(self.calls.items()))
        if "from equity.call_turn" in s:
            return Cur([(0, 0, 10, "Moderator", "moderator", "opening", 10)] if self.calls_by_doc(params[0])["chunked"] else [])
        if "from core.chunk" in s:
            return Cur([(0, 0, 10, "Opening remarks", "Moderator")] if self.calls_by_doc(params[0])["chunked"] else [])
        raise AssertionError(s)

    def calls_by_doc(self, doc):
        return next(c for c in self.calls.values() if c["doc"] == doc)


class FakeFetcher:
    def __init__(self, db):
        self.db, self.reads = db, []

    def read(self, raw_id):
        self.reads.append(raw_id)
        return b"x" * 1024


class FakeTx:
    """What `load` writes: records the statements; `analysis_version is distinct from` is honoured on the call table."""

    def __init__(self, versions):
        self.versions, self.sql = versions, []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append(s)
        if s.startswith("update equity.earnings_call set analysis_version"):
            version, event_id, _ = params
            changed = self.versions.get(event_id) != version
            self.versions[event_id] = version
            return Cur(rowcount=int(changed))
        if s.startswith("update core."):
            return Cur(rowcount=0)                                       # already published
        raise AssertionError(s)


@pytest.fixture
def rules(monkeypatch):
    """The text, turns and chunks of every fake PDF: one moderator turn of ten words."""
    ex = SimpleNamespace(sha256=SHA, text="0123456789")
    monkeypatch.setattr(pdfs, "read_pdf", lambda b: b)
    monkeypatch.setattr(pdfs, "call_text", lambda info: SimpleNamespace(extracted=ex, labels=[]))
    turn = turns.Turn(0, 0, 10, "Moderator", "moderator", 0, 1, 10, "opening", "moderator")
    monkeypatch.setattr(ect.turns, "analyse", lambda e, labels: turns.Analysis(
        [turn], [(True, "Moderator", "Opening remarks")], True, False, 10, 0, []))
    monkeypatch.setattr(ect, "chunk_text", lambda e, **kw: [Chunk(0, 0, 10, "Opening remarks", "Moderator", "0123456789")])
    return ex


def run_fetch(calls, now=NOW, has_column=True):
    db = FakeDb(calls, has_column)
    ctx = SimpleNamespace(now=now, fetcher=FakeFetcher(db))
    task = ect.EquityCallText()
    return task, task.fetch(ctx), ctx.fetcher


def chunked(eid, version=THIS, **kw):
    return {"doc": eid, "version": version, "chunked": True, **kw}


def test_a_chunked_call_with_the_current_version_outside_the_slice_is_not_read_and_nothing_is_written(rules):
    eid = an_id(False)
    task, h, fetcher = run_fetch({eid: chunked(eid)})
    assert fetcher.reads == [] and h.files_read == 0 and h.bytes_read == 0 and h.reasons == {}
    assert [c.skipped for c in h.calls] == [True] and h.version == THIS and h.versioned
    tx = FakeTx({eid: THIS})
    assert task.load(tx, h, 1) == 0 and task.done == []
    assert all(s.startswith("update core.") for s in tx.sql)                        # only the "published again" updates


def test_a_call_with_no_version_is_read_and_its_version_written_then_a_second_run_reads_nothing(rules):
    eid = an_id(False)
    task, h, fetcher = run_fetch({eid: chunked(eid, version=None)})
    assert fetcher.reads == [eid + 1000] and h.reasons == {"version": 1} and not h.calls[0].skipped
    assert h.calls[0].unchanged and h.calls[0].problem is None
    versions = {}
    tx = FakeTx(versions)
    assert task.load(tx, h, 1) == 1 and versions == {eid: THIS}
    task2, h2, fetcher2 = run_fetch({eid: chunked(eid, version=versions[eid])})           # the next run
    tx2 = FakeTx(versions)
    assert fetcher2.reads == [] and task2.load(tx2, h2, 2) == 0 and versions == {eid: THIS}
    assert not any("analysis_version" in s for s in tx2.sql)


def test_a_new_call_is_read(rules):
    eid = an_id(False)
    _, h, fetcher = run_fetch({eid: {"doc": eid, "version": None, "chunked": False}})
    assert fetcher.reads == [eid + 1000] and h.reasons == {"new": 1}
    assert h.calls[0].analysis is not None and h.calls[0].refresh is False


def test_a_call_in_the_runs_slice_is_read_but_unchanged_so_nothing_is_written(rules):
    eid = an_id(True)
    task, h, fetcher = run_fetch({eid: chunked(eid)})
    assert fetcher.reads == [eid + 1000] and h.reasons == {"slice": 1} and h.calls[0].unchanged
    assert task.load(FakeTx({eid: THIS}), h, 1) == 0


def test_a_changed_rule_makes_every_call_read_once(rules, monkeypatch):
    ids = [an_id(False), an_id(False, after=100), an_id(True)]
    calls = {i: chunked(i) for i in ids}
    _, h, fetcher = run_fetch(calls)
    assert fetcher.reads == [ids[2] + 1000] and h.reasons == {"slice": 1}                  # only the slice, rules unchanged
    monkeypatch.setattr(ect, "analysis_version", lambda: "a-new-version")
    _, h2, fetcher2 = run_fetch(calls)
    assert sorted(fetcher2.reads) == sorted(i + 1000 for i in ids) and h2.reasons == {"version": 3}


def test_over_four_weekly_runs_every_call_is_read_exactly_once_and_no_run_reads_more_than_its_slice(rules):
    ids = list(range(1, 41))
    calls = {i: chunked(i) for i in ids}
    seen = []
    for k in range(SLICES):
        _, h, fetcher = run_fetch(calls, now=at(5 + k))                   # from 20 Dec 2026: across New Year
        assert set(fetcher.reads) == {i + 1000 for i in ids if ect.in_slice(i, at(5 + k).date(), SLICES)}
        assert h.reasons in ({"slice": len(fetcher.reads)}, {})
        assert 0 < len(fetcher.reads) < len(ids) * 0.4
        seen += fetcher.reads
    assert sorted(seen) == sorted(i + 1000 for i in ids)


def test_a_re_derived_file_that_no_longer_hashes_to_the_stored_text_still_fails_text_matches(rules):
    eid = an_id(False)
    task, h, _ = run_fetch({eid: chunked(eid, version="an-older-version", sha=bytes(32))})
    assert h.calls[0].problem == "text_mismatch"
    c = {x.name: x for x in task.checks(None, h, 1)}
    assert not c["text_matches"].passed and c["text_matches"].blocking and f"zf-{eid}" in c["text_matches"].detail
    assert task.load(FakeTx({}), h, 1) == 0                                      # nothing kept; the run fails on the check


def test_the_checks_say_what_was_skipped_and_read(rules):
    out, inside = an_id(False), an_id(True)
    task, h, _ = run_fetch({out: chunked(out), inside: chunked(inside)})
    c = {x.name: x for x in task.checks(None, h, 1)}
    assert c["text_matches"].passed and c["text_matches"].blocking
    assert f"1 transcripts re-derived to their stored hash; 1 skipped, analysed under version {THIS}" in c["text_matches"].detail
    assert "1 stored files read (0.0 MB): 1 in this run's integrity slice" in c["text_matches"].detail
    assert c["calls_analysed"].passed and not c["calls_analysed"].blocking
    assert "1 calls analysed (1 with a hand-over)" in c["calls_analysed"].detail and "1 skipped" in c["calls_analysed"].detail
    assert "(of the 1 calls re-derived in this run; 1 skipped)" in c["unknown_roles"].detail
    assert "(of the 1 calls re-derived in this run; 1 skipped)" in c["no_handover"].detail
    assert c["analysis_versions"].passed and not c["analysis_versions"].blocking
    assert set(c) == {"text_matches", "chunks_reproduce", "turns_contiguous", "unknown_roles", "no_handover", "quarter_conflicts",
                      "analysis_versions", "calls_analysed"}


def test_without_the_column_every_call_is_read_nothing_is_recorded_and_a_warning_says_so(rules):
    ids = [an_id(False), an_id(False, after=100)]
    task, h, fetcher = run_fetch({i: chunked(i, version=None) for i in ids}, has_column=False)
    assert sorted(fetcher.reads) == sorted(i + 1000 for i in ids) and h.reasons == {"legacy": 2} and not h.versioned
    tx = FakeTx({})
    assert task.load(tx, h, 1) == 0 and not any("analysis_version" in s for s in tx.sql)
    c = {x.name: x for x in task.checks(None, h, 1)}
    assert not c["analysis_versions"].passed and not c["analysis_versions"].blocking and "0033" in c["analysis_versions"].detail
    assert c["calls_analysed"].passed and "migration 0033 not applied" in c["calls_analysed"].detail
    assert c["text_matches"].passed


def test_a_call_with_the_current_version_that_was_set_aside_and_restored_is_still_published_again(rules):
    eid = an_id(False)
    task, h, fetcher = run_fetch({eid: chunked(eid)})

    class Tx(FakeTx):
        def execute(self, sql, params=None):
            if "update core.document" in sql:
                self.sql.append("doc")
                return Cur(rowcount=1)
            if "update core.event" in sql:
                self.sql.append("event")
                return Cur(rowcount=1)
            return super().execute(sql, params)

    tx = Tx({eid: THIS})
    assert fetcher.reads == [] and task.load(tx, h, 1) == 2 and tx.sql == ["doc", "event"]
