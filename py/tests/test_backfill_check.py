"""The backfill re-run check (Prompt 01 B8, part 1): a re-run that adds a raw file, a stored object, a document or an AI
request fails, and so does a raw file that doesn't read back; a clean re-run passes. Storage is a local S3-protocol
server (moto); the database tests run as job_writer against the test database, the backfill replaced by a fake."""
import argparse
import uuid
from datetime import UTC, datetime

import boto3
import psycopg
import pytest
import yaml
from conftest import dsn
from moto.server import ThreadedMotoServer
from test_workflows import WORKFLOWS, resolve, secrets

from ft.jobs import backfill_check as bc
from ft.storage import Storage

ADMIN, JOBS = dsn("postgres"), dsn("job_writer")
PORT = 5606
ENDPOINT = f"http://127.0.0.1:{PORT}"


# ---------------------------------------------------------------- without the database

def result(task="policy.calendar", code=0, **delta):
    d = {k: 0 for k in [*bc.COUNTS, "stored_objects"]} | delta
    return bc.TaskResult(task, code, d, ["fed: https://example.org/a (10 bytes)"] if delta.get("raw_files") else [],
                         ["fomc_statement x v2"] if delta.get("documents") else [])


def test_a_clean_rerun_passes_and_new_observations_alone_are_only_reported():
    out = []
    assert bc.report([result(), result("policy.market_series", observations=5, chunks=3)], 12, [], out.append)
    assert out[-1] == "::notice title=backfill check::passed"
    assert "all 12 read back hash-verified" in out[-2]


@pytest.mark.parametrize("grew", ["raw_files", "stored_objects", "documents", "ai_requests"])
def test_a_rerun_that_adds_what_b8_forbids_fails(grew):
    out = []
    assert not bc.report([result(**{grew: 1})], 0, [], out.append)
    assert out[0].startswith("::error") and out[-1] == "::error title=backfill check::failed"
    if grew == "raw_files":
        assert any("new raw files: fed: https://example.org/a" in line for line in out)


FRED = "https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&observation_start=2019-01-01&file_type=json"
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/%5ENSEI?period1=1&period2=2&interval=1d"
RBI = "https://www.rbi.org.in/Scripts/Annualpolicy.aspx"


def test_only_the_named_changing_sources_are_volatile():
    assert bc.is_volatile("fred", FRED) and bc.is_volatile("yfinance", YAHOO) and bc.is_volatile("rbi", RBI)
    assert not bc.is_volatile("rbi", RBI + "#hdnYear=2020")              # a closed fiscal year must not change
    assert not bc.is_volatile("rbi", "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1")
    assert not bc.is_volatile("fed", FRED)                               # the source must match too
    assert not bc.is_volatile("fred", "https://fred.stlouisfed.org/series/DGS10")
    assert not bc.is_volatile("fred", None)


def test_new_raw_files_from_changing_sources_pass_and_are_reported():
    out = []
    r = bc.TaskResult("policy.market_series", 0, {"raw_files": 17, "stored_objects": 17, "documents": 0, "ai_requests": 0},
                      volatile=17, volatile_listed=[f"fred: {FRED} (10 bytes)"])
    assert r.ok and bc.report([r], 0, [], out.append)
    assert "17 from sources whose bytes change on every fetch" in out[0]
    assert out[1].startswith("::notice") and FRED in out[1]


@pytest.mark.parametrize("delta", [{"raw_files": 2, "stored_objects": 2}, {"raw_files": 1, "stored_objects": 2},
                                   {"raw_files": 1, "stored_objects": 1, "documents": 1}])
def test_anything_beyond_the_changing_sources_still_fails(delta):
    r = bc.TaskResult("policy.calendar", 0, {"ai_requests": 0, "documents": 0} | delta, volatile=1)
    assert not r.ok and not bc.report([r], 0, [], lambda _: None)


def test_a_failed_run_or_a_bad_raw_file_fails():
    assert not bc.report([result(code=1)], 0, [], lambda _: None)
    out = []
    assert not bc.report([], 3, ["raw/fed/ab/ab.html.gz: content does not match its hash"], out.append)
    assert "1 of 3 failed to read back" in out[0]


def test_an_uncounted_object_store_is_not_a_failure():
    assert bc.report([result(stored_objects=None)], 0, [], lambda _: None)


def test_arguments():
    a = bc.parser().parse_args([])
    assert a.tasks == list(bc.TASKS) and a.since.isoformat() == "2019-01-01" and a.until is None and a.target == "dev"
    a = bc.parser().parse_args(["--tasks", " policy.calendar , ", "--until", "", "--github-run-id", ""])
    assert a.tasks == ["policy.calendar"] and a.until is None and a.github_run_id is None
    for bad in (["--tasks", ","], ["--since", "2019-13-01"], ["--target", "staging"]):
        with pytest.raises(SystemExit):
            bc.parser().parse_args(bad)
    with pytest.raises(SystemExit):                                   # no database URL
        bc.main([], env={}, storage=object())
    with pytest.raises(SystemExit):
        bc.main(["--since", "2020-01-02", "--until", "2020-01-01"], env={"JOB_DATABASE_URL": "x"}, storage=object())


def test_the_backfill_runs_as_backfill_yml_runs_it(monkeypatch):
    seen = {}

    def fake_main(argv, env):
        seen["argv"], seen["env"] = argv, env
        return 0

    from ft.jobs import run
    monkeypatch.setattr(run, "main", fake_main)
    args = bc.parser().parse_args(["--until", "2026-10-24", "--target", "dev", "--runner", "github",
                                   "--github-run-id", "7", "--git-sha", "abc"])
    assert bc.run_backfill("policy.fed_documents", args, {"X": "1"}) == 0
    assert seen["argv"] == ["--workflow", "backfill", "--task", "policy.fed_documents", "--target", "dev",
                            "--runner", "github", "--since", "2019-01-01", "--until", "2026-10-24",
                            "--github-run-id", "7", "--git-sha", "abc"]

    def refused(argv, env):
        raise SystemExit(2)
    monkeypatch.setattr(run, "main", refused)
    assert bc.run_backfill("policy.fed_documents", args, {}) == 2


# ---------------------------------------------------------------- the workflow

def test_the_workflow_is_manual_shares_backfills_group_and_takes_inputs_only_through_env():
    wf = yaml.safe_load((WORKFLOWS / "backfill-check.yml").read_text())
    on = wf.get("on", wf.get(True))
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["target"]["default"] == "dev" and inputs["target"]["options"] == ["dev", "production"]
    assert inputs["tasks"]["default"].split(",") == list(bc.TASKS)
    assert wf["concurrency"]["group"] == yaml.safe_load((WORKFLOWS / "backfill.yml").read_text())["concurrency"]["group"]
    step = wf["jobs"]["run"]["steps"][-1]
    assert "inputs." not in step["run"] and "github.event.inputs" not in step["run"]
    assert not [k for k in step["env"] if k.startswith(("ANTHROPIC", "VOYAGE"))]        # no AI key: no AI spend


@pytest.mark.parametrize("inputs", [{}, {"target": "dev"}, {"target": ""}, {"target": "DEV"}])
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_the_workflow_reaches_production_only_when_chosen(inputs, dev_secrets):
    job_env, steps = resolve("backfill-check", "workflow_dispatch", inputs, secrets(dev_secrets))
    step = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert job_env["FT_TARGET"] == "dev"
    assert step["JOB_DATABASE_URL"] == ("DEV:JOB_DATABASE_URL" if dev_secrets else "")
    assert step["STORAGE_SECRET_ACCESS_KEY"] == ("DEV:STORAGE_SECRET_ACCESS_KEY" if dev_secrets else "")
    assert not [v for e in [job_env, *steps] for v in e.values() if "PROD:" in v]
    job_env, steps = resolve("backfill-check", "workflow_dispatch", {"target": "production"}, secrets(dev_secrets))
    assert next(e for e in steps if "JOB_DATABASE_URL" in e)["JOB_DATABASE_URL"] == "PROD:JOB_DATABASE_URL"


# ---------------------------------------------------------------- against the test database

@pytest.fixture(scope="module")
def s3():
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    client = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    yield client
    srv.stop()


@pytest.fixture
def storage(s3):
    bucket = f"b8-{uuid.uuid4().hex[:12]}"
    s3.create_bucket(Bucket=bucket)
    return Storage(ENDPOINT, bucket, "test", "test", region="us-east-1")


@pytest.fixture
def db():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        jr = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('backfill','cli','local') returning id").fetchone()[0]
        tr = c.execute("insert into ops.task_run (job_run_id, task_key) values (%s, 'policy.fed_documents') returning id",
                       (jr,)).fetchone()[0]
        source = c.execute("select id from core.source where key = 'fed'").fetchone()[0]
        floor = c.execute("select coalesce(max(id), 0) from core.raw_file").fetchone()[0]
    return {"task_run": tr, "source": source, "floor": floor}


def register(db, storage, body: bytes, source: str = "fed", url: str | None = None) -> str:
    """Stores `body` and registers it, as the shared fetcher does."""
    o = storage.put(body, source=source, media_type="text/html", ext="html")
    with psycopg.connect(ADMIN, autocommit=True) as c:
        source_id = c.execute("select id from core.source where key = %s", (source,)).fetchone()[0]
        c.execute("""insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at,
                                                task_run_id)
                     values (%s, %s, %s, %s, %s, 'text/html', %s, %s)""",
                  (o.object_key, source_id, url or f"https://example.org/{o.sha256.hex()[:8]}", o.sha256, o.bytes,
                   datetime.now(UTC), db["task_run"]))
    return o.object_key


def page(text: str) -> bytes:
    return f"<html><body>{text} {uuid.uuid4()}</body></html>".encode()


def check(db, storage, backfill, tasks="policy.calendar"):
    return bc.main(["--tasks", tasks], env={"JOB_DATABASE_URL": JOBS}, storage=storage, backfill=backfill,
                   verify_after_id=db["floor"])


@pytest.mark.db
def test_a_rerun_that_writes_nothing_passes_and_every_raw_file_reads_back(db, storage, capsys):
    for i in range(3):
        register(db, storage, page(f"statement {i}"))
    calls = []
    assert check(db, storage, lambda t, a, e: calls.append(t) or 0, "policy.calendar,policy.fed_documents") == 0
    assert calls == ["policy.calendar", "policy.fed_documents"]
    out = capsys.readouterr().out
    assert "raw files: all 3 read back hash-verified" in out and "stored_objects +0" in out
    assert out.rstrip().endswith("::notice title=backfill check::passed")


@pytest.mark.db
def test_a_rerun_that_stores_a_new_raw_file_fails_and_lists_it(db, storage, capsys):
    register(db, storage, page("statement"))
    assert check(db, storage, lambda t, a, e: register(db, storage, page("changed")) and 0) == 1
    out = capsys.readouterr().out
    assert "raw_files +1" in out and "stored_objects +1" in out
    assert "policy.calendar new raw files: fed: https://example.org/" in out
    assert out.rstrip().endswith("::error title=backfill check::failed")


@pytest.mark.db
def test_a_rerun_that_stores_only_changing_responses_passes(db, storage, capsys):
    def rerun(t, a, e):
        register(db, storage, page("fred json"), "fred", FRED)
        register(db, storage, page("rbi page"), "rbi", RBI)
        return 0
    assert check(db, storage, rerun) == 0
    out = capsys.readouterr().out
    assert "raw_files +2" in out and "2 from sources whose bytes change on every fetch" in out
    assert out.rstrip().endswith("::notice title=backfill check::passed")


@pytest.mark.db
def test_a_closed_rbi_year_that_changes_still_fails(db, storage, capsys):
    assert check(db, storage, lambda t, a, e: register(db, storage, page("2020"), "rbi", RBI + "#hdnYear=2020") and 0) == 1
    assert "new raw files: rbi: " + RBI + "#hdnYear=2020" in capsys.readouterr().out


@pytest.mark.db
def test_a_failed_task_run_fails_the_check(db, storage):
    assert check(db, storage, lambda t, a, e: 1) == 1


@pytest.mark.db
def test_a_corrupted_or_missing_object_fails_the_read_back(db, storage, s3, capsys):
    key = register(db, storage, page("statement"))
    gone = register(db, storage, page("minutes"))
    s3.put_object(Bucket=storage.bucket, Key=key, Body=b"not the bytes")           # replaced behind the function's back
    s3.delete_object(Bucket=storage.bucket, Key=gone)
    with psycopg.connect(JOBS) as c:
        checked, failures = bc.verify(c, storage, after_id=db["floor"])
    assert checked == 2 and len(failures) == 2
    assert any(f.startswith(gone) and "not readable" in f for f in failures)
    assert any(f.startswith(key) for f in failures)


@pytest.mark.db
def test_a_row_whose_size_disagrees_with_its_object_fails(db, storage):
    key = register(db, storage, page("statement"))
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("update core.raw_file set bytes = bytes + 1 where object_key = %s", (key,))
    with psycopg.connect(JOBS) as c:
        checked, failures = bc.verify(c, storage, after_id=db["floor"])
    assert checked == 1 and len(failures) == 1
    assert failures[0].startswith(key) and "bytes read, the row says" in failures[0]


def test_parser_namespace_is_what_run_backfill_reads():
    a = bc.parser().parse_args([])
    assert isinstance(a, argparse.Namespace) and {"since", "until", "target", "runner", "github_run_id", "git_sha"} <= set(vars(a))
