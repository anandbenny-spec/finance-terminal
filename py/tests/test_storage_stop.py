"""F3-02 (#387): the storage stop. `ft.storage.StorageStop` answers "may this task store a new file now?" for the tasks that opt in
(the earnings-call transcripts; BRSR when it exists); module 1's document tasks never meet it, and `Storage.put` never asks it.
The task-level behaviour (no PDF fetched or stored over the stop, discovery still recorded, the error alert) is in
test_equity_call_fetch.py, which needs the database and the saved files."""
import re
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from moto.server import ThreadedMotoServer

from ft.equity.calls import discovery as dc
from ft.equity.calls import pipeline as pl
from ft.storage import MB, Storage, StorageStop

PORT = 5596
ENDPOINT = f"http://127.0.0.1:{PORT}"
SRC = Path(__file__).resolve().parents[1] / "src" / "ft"


class Sized:
    """A stand-in for the object store: `stored_bytes()` answers `size`, or raises when `size` is an exception."""

    def __init__(self, size):
        self.size, self.listed = size, 0

    def stored_bytes(self):
        self.listed += 1
        if isinstance(self.size, Exception):
            raise self.size
        return 7, self.size


# ---------------------------------------------------------------------------------------------- the stop itself
def test_under_the_stop_new_files_are_allowed_and_the_bucket_is_listed_once():
    st = Sized(676 * MB)
    stop = StorageStop(st, 900)
    assert stop.readable and stop.allows() and stop.stored == 676 * MB
    for _ in range(3):
        stop.allows()
    assert st.listed == 1                                          # listed once per run, never per file
    assert stop.describe() == "the raw bucket holds 676.0 MB of stored objects, under the 900 MB stop for new files"


def test_each_stored_file_counts_so_the_stop_is_reached_within_the_run():
    stop = StorageStop(Sized(899 * MB), 900)
    assert stop.allows()
    stop.add(MB // 2)
    assert stop.allows()
    stop.add(MB // 2)                                              # exactly at the stop: held
    assert not stop.allows() and stop.stored == 900 * MB
    assert stop.describe().endswith("900.0 MB of stored objects, at or over the 900 MB stop for new files")
    stop.add(-5)                                                   # a negative size never lowers the count
    assert stop.stored == 900 * MB


def test_over_the_stop_new_files_are_held():
    stop = StorageStop(Sized(950 * MB), 900)
    assert stop.readable and not stop.allows()


@pytest.mark.parametrize("storage", [Sized(RuntimeError("listing refused")), None, lambda: (_ for _ in ()).throw(OSError("no bucket"))],
                         ids=["listing raises", "no storage", "factory raises"])
def test_a_bucket_that_cant_be_listed_holds_new_files_with_its_own_message(storage):
    stop = StorageStop(storage, 900)
    assert not stop.readable and not stop.allows() and stop.stored is None
    stop.add(10)                                                   # still unknown: adding to an unknown total doesn't open it
    assert not stop.allows()
    assert stop.describe() == ("the raw bucket's size couldn't be read, so new files are held (the stop fails closed; "
                               "read Storage in the dashboard; the stop is 900 MB)")
    assert "listing refused" not in stop.describe() and "no bucket" not in stop.describe()      # never the raw error


def test_a_factory_is_called_once():
    calls = []

    def factory():
        calls.append(1)
        return Sized(10 * MB)
    assert StorageStop(factory, 900).allows() and calls == [1]


def test_the_stop_must_be_positive():
    with pytest.raises(ValueError):
        StorageStop(Sized(0), 0)


# ---------------------------------------------------------------------------------------------- opt-in only
@pytest.fixture(scope="module")
def server():
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
    s3.create_bucket(Bucket="stop-raw")
    yield s3
    srv.stop()


def test_the_stop_reads_the_real_listing_and_put_never_asks_it(server):
    st = Storage(ENDPOINT, "stop-raw", "test", "test", region="us-east-1")
    a = st.put(b"%PDF-1.7\n" + bytes(range(256)) * 8, source="nse", media_type="application/pdf", ext="pdf")
    stop = StorageStop(st, 1)
    assert stop.stored == a.stored_bytes and stop.allows()
    stop.add(MB)                                                   # now over its 1 MB stop
    assert not stop.allows()
    b = st.put(b"%PDF-1.7\n" + bytes(range(256)) * 9, source="fed", media_type="application/pdf", ext="pdf")
    assert b.created and st.get(b.object_key)                      # module 1's store isn't touched by any stop
    assert StorageStop(st, 1).stored == a.stored_bytes + b.stored_bytes


def _users_of_the_stop() -> set[str]:
    return {str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
            if re.search(r"\bStorageStop\b", p.read_text(encoding="utf-8"))}


def test_only_the_growth_tasks_consult_the_stop_and_module_1_never_does():
    users = _users_of_the_stop()
    growth = {"storage.py", "jobs/tasks/equity_call_transcripts.py", "jobs/tasks/equity_brsr.py"}
    assert "jobs/tasks/equity_call_transcripts.py" in users
    assert users <= growth, f"only the growth tasks opt in to the storage stop: {sorted(users - growth)}"
    for module_1 in ("jobs/tasks/fed_documents.py", "jobs/tasks/rbi_documents.py", "engines/document/job.py",
                     "jobs/fetch.py", "jobs/runner.py"):
        assert (SRC / module_1).exists(), module_1                 # the scan would pass vacuously for a renamed file
        assert module_1 not in users


# ---------------------------------------------------------------------------------------------- the config
def test_the_hold_sits_above_the_850_mb_warning_which_is_unchanged(tmp_path):
    cfg = dc.load_calls_config()
    assert (cfg.raw_storage_stop_mb, cfg.raw_storage_hold_mb) == (850, 900)
    bad = tmp_path / "calls.yaml"
    text = dc.CALLS_CONFIG.read_text(encoding="utf-8")
    bad.write_text(text.replace("raw_storage_hold_mb: 900", "raw_storage_hold_mb: 850"), encoding="utf-8")
    with pytest.raises(ValueError, match="above raw_storage_stop_mb"):
        dc.load_calls_config(bad)


def test_the_850_mb_warning_still_fires_over_850():
    ok, detail = pl.storage_summary(3, 2 * MB, 860 * MB, 850)
    assert not ok and detail.endswith("the raw bucket holds 860.0 MB of stored objects, OVER the 850 MB stop")


# ---------------------------------------------------------------------------------------------- the held outcome
CO = pl.CompanyRef(1, "asian-paints", "Asian Paints Limited", "ASIANPAINT")


def ann(key, season, kind="transcript", entity_id=1):
    return dc.Announcement("NSE", key, "ASIANPAINT", "INE021A01026", None, datetime(2026, 8, 1, tzinfo=UTC), "Transcript",
                           f"{key}.pdf", f"https://nsearchives.nseindia.com/{key}.pdf", kind, "subject", season, entity_id)


ANNS = [ann("a1", "Q3 FY26"), ann("a2", "Q4 FY26"), ann("a3", "Q1 FY27"), ann("a4", "Q1 FY27"), ann("a5", "Q2 FY27"),
        ann("i1", "Q2 FY27", kind="intimation"), ann("x1", "Q2 FY27", entity_id=2)]


def test_a_backfill_names_every_season_with_transcript_filings_oldest_first():
    assert pl.held_by_storage(CO, ANNS, weekly=False, newest_call=None, history=True) == [
        "asian-paints Q3 FY26: 1 transcript filing, held: storage stop",
        "asian-paints Q4 FY26: 1 transcript filing, held: storage stop",
        "asian-paints Q1 FY27: 2 transcript filings, held: storage stop",
        "asian-paints Q2 FY27: 1 transcript filing, held: storage stop"]


def test_a_weekly_run_names_only_the_seasons_newer_than_the_newest_call_and_at_most_two():
    assert pl.held_by_storage(CO, ANNS, weekly=True, newest_call="Q1 FY27") == [
        "asian-paints Q2 FY27: 1 transcript filing, held: storage stop"]
    assert pl.held_by_storage(CO, ANNS, weekly=True, newest_call="Q3 FY26") == [
        "asian-paints Q1 FY27: 2 transcript filings, held: storage stop",
        "asian-paints Q2 FY27: 1 transcript filing, held: storage stop"]
    assert pl.held_by_storage(CO, ANNS, weekly=True, newest_call="Q2 FY27") == []
    assert pl.held_by_storage(CO, ANNS, weekly=False, newest_call=None) == [            # a refresh: the two newest
        "asian-paints Q1 FY27: 2 transcript filings, held: storage stop",
        "asian-paints Q2 FY27: 1 transcript filing, held: storage stop"]


def test_a_company_with_no_transcript_filing_has_nothing_held():
    assert pl.held_by_storage(CO, [ann("i1", "Q2 FY27", kind="intimation")], weekly=True, newest_call=None) == []
