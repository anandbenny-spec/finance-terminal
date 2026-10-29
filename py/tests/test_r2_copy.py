"""2B-01 (#164): the copy from Supabase Storage to R2 and the per-target backend setting. Two local S3-protocol servers'
buckets (moto) stand in for Supabase Storage and R2; the database is a fake that returns core.raw_file rows."""
import gzip
import hashlib
from contextlib import contextmanager

import boto3
import pytest
import yaml
from moto.server import ThreadedMotoServer
from test_workflows import WORKFLOWS, resolve, secrets

from ft import storage as storage_mod
from ft.jobs import r2_copy as rc
from ft.storage import BACKENDS, CONFIG_PATH, IntegrityError, Storage, backend_for

PORT = 5611
ENDPOINT = f"http://127.0.0.1:{PORT}"
HTML = ("<html><body>" + "The Committee decided to hold the target range. " * 300 + "</body></html>").encode()
PDF = b"%PDF-1.7\n" + bytes(range(256)) * 30


@pytest.fixture(scope="module")
def s3():
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    yield boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                       aws_access_key_id="test", aws_secret_access_key="test")
    srv.stop()


@pytest.fixture
def pair(s3):
    n = hashlib.sha256(str(id(s3)).encode() + str(len(s3.list_buckets()["Buckets"])).encode()).hexdigest()[:10]
    src_b, dst_b = f"supabase-{n}", f"r2-{n}"
    s3.create_bucket(Bucket=src_b)
    s3.create_bucket(Bucket=dst_b)
    return tuple(Storage(ENDPOINT, b, "test", "test", region="us-east-1") for b in (src_b, dst_b))


def put(src, data, source, media_type, ext):
    o = src.put(data, source=source, media_type=media_type, ext=ext)
    return rc.Row(o.object_key, o.sha256, o.bytes)


# ---------------------------------------------------------------- the copy

def test_every_object_is_copied_under_its_key_and_reads_back(pair):
    src, dst = pair
    r = [put(src, HTML, "fed", "text/html", ".html"), put(src, PDF, "rbi", "application/pdf", ".pdf")]
    c = rc.copy_all(iter(r), src, dst)
    assert (c.objects, c.copied, c.there, c.mismatched) == (2, 2, 0, [])
    assert c.bytes == sum(src._s3.head_object(Bucket=src.bucket, Key=x.object_key)["ContentLength"] for x in r)
    assert sorted(src.iter_keys()) == sorted(dst.iter_keys())             # same keys: core.raw_file needs no update
    assert dst.get(r[0].object_key) == HTML and dst.get(r[1].object_key) == PDF
    meta = dst._s3.head_object(Bucket=dst.bucket, Key=r[0].object_key)
    assert meta["Metadata"]["sha256"] == r[0].sha256.hex() and meta["ContentType"] == "application/gzip"


def test_a_resumed_copy_copies_nothing_and_reads_the_rest_back(pair):
    src, dst = pair
    r = [put(src, HTML, "fed", "text/html", ".html"), put(src, PDF, "rbi", "application/pdf", ".pdf")]
    rc.copy_all(iter(r[:1]), src, dst)                                    # an interrupted first run
    c = rc.copy_all(iter(r), src, dst)
    assert (c.copied, c.there) == (1, 1) and not c.mismatched
    again = rc.copy_all(iter(r), src, dst)
    assert (again.copied, again.there) == (0, 2)


def test_a_dry_run_writes_nothing(pair):
    src, dst = pair
    r = [put(src, HTML, "fed", "text/html", ".html")]
    c = rc.copy_all(iter(r), src, dst, dry_run=True)
    assert (c.copied, c.mismatched) == (1, []) and list(dst.iter_keys()) == []


def test_a_corrupt_supabase_object_is_a_mismatch_and_is_not_copied(pair):
    src, dst = pair
    good = put(src, PDF, "rbi", "application/pdf", ".pdf")
    bad = put(src, b'{"rate": 5.25}', "fred", "application/json", ".json")
    src._s3.put_object(Bucket=src.bucket, Key=bad.object_key, Body=gzip.compress(b'{"rate": 9.99}'))
    c = rc.copy_all(iter([good, bad]), src, dst)
    assert (c.copied, len(c.mismatched)) == (1, 1)
    assert c.mismatched[0].key == bad.object_key and "Supabase copy" in c.mismatched[0].reason
    assert list(dst.iter_keys()) == [good.object_key]


def test_an_r2_object_with_the_wrong_content_is_a_mismatch_and_is_never_overwritten(pair):
    src, dst = pair
    row = put(src, PDF, "rbi", "application/pdf", ".pdf")
    dst._s3.put_object(Bucket=dst.bucket, Key=row.object_key, Body=b"tampered")
    c = rc.copy_all(iter([row]), src, dst)
    assert len(c.mismatched) == 1 and "R2 copy" in c.mismatched[0].reason and c.copied == 0
    assert dst._s3.get_object(Bucket=dst.bucket, Key=row.object_key)["Body"].read() == b"tampered"   # left for Anand


def test_the_row_must_agree_with_the_object(pair):
    src, dst = pair
    row = put(src, PDF, "rbi", "application/pdf", ".pdf")
    wrong_hash = rc.Row(row.object_key, hashlib.sha256(b"other").digest(), row.bytes)
    wrong_size = rc.Row(row.object_key, row.sha256, row.bytes + 1)
    for r, why in ((wrong_hash, "hash in core.raw_file"), (wrong_size, "core.raw_file's")):
        c = rc.copy_all(iter([r]), src, dst)
        assert len(c.mismatched) == 1 and why in c.mismatched[0].reason
    assert list(dst.iter_keys()) == []


def test_a_row_without_an_object_is_a_mismatch(pair):
    src, dst = pair
    ghost = rc.Row("raw/fed/aa/" + "a" * 64 + ".html.gz", b"\xaa" * 32, 10)
    c = rc.copy_all(iter([ghost]), src, dst)
    assert len(c.mismatched) == 1 and "unreadable" in c.mismatched[0].reason


def test_a_write_that_reads_back_wrong_is_retried_once_then_reported(pair, monkeypatch):
    src, dst = pair
    row = put(src, PDF, "rbi", "application/pdf", ".pdf")
    writes = []
    real = dst._s3.put_object

    def flaky(**kw):
        writes.append(1)
        return real(**{**kw, "Body": kw["Body"][:-1]} if len(writes) == 1 else kw)      # only the first write is cut short

    monkeypatch.setattr(dst._s3, "put_object", flaky)
    c = rc.copy_all(iter([row]), src, dst)
    assert (c.copied, c.mismatched, len(writes)) == (1, [], 2)                            # the retry fixed it

    row2 = put(src, HTML, "fed", "text/html", ".html")
    monkeypatch.setattr(dst._s3, "put_object", lambda **kw: real(**{**kw, "Body": b"cut"}))
    c = rc.copy_all(iter([row2]), src, dst)
    assert len(c.mismatched) == 1 and "after a retry" in c.mismatched[0].reason


def test_nothing_is_ever_deleted(pair, monkeypatch):
    src, dst = pair
    for s in (src, dst):
        monkeypatch.setattr(s._s3, "delete_object", lambda **kw: pytest.fail("delete_object called"))
        monkeypatch.setattr(s._s3, "delete_objects", lambda **kw: pytest.fail("delete_objects called"))
    rc.copy_all(iter([put(src, HTML, "fed", "text/html", ".html")]), src, dst)


def test_the_report_names_each_mismatch_and_fails():
    c = rc.Counts(objects=3, bytes=30, copied=1, there=1, mismatched=[rc.Outcome("mismatch", "raw/x/aa/b.pdf", 5, "why")])
    out = []
    assert rc.report(c, "dev", False, out.append) is False
    assert "objects 3, bytes 30, copied 1, already there 1, mismatched 1" in out[0]
    assert "raw/x/aa/b.pdf: why" in out[1] and out[-1].endswith("failed")
    out = []
    assert rc.report(rc.Counts(objects=1, copied=1), "dev", True, out.append) is True
    assert "would copy 1" in out[0] and out[-1].endswith("passed")


# ---------------------------------------------------------------- reading the rows, and main()

class FakeConn:
    def __init__(self, data):
        self.data, self.calls = data, []

    def execute(self, sql, params):
        self.calls.append(params)
        after, limit = params
        found = [r for r in self.data if r[0] > after][:limit]
        return type("Result", (), {"fetchall": lambda _self: found})()


def test_rows_pages_through_core_raw_file_in_id_order():
    data = [(i, f"raw/k{i}", bytes([i]) * 32, i) for i in range(1, 8)]
    got = list(rc.rows(FakeConn(data), page=3))
    assert [r.object_key for r in got] == [f"raw/k{i}" for i in range(1, 8)]
    assert got[0].sha256 == bytes([1]) * 32 and got[2].bytes == 3


def test_main_needs_the_database_and_both_sets_of_settings(capsys):
    with pytest.raises(SystemExit):
        rc.main(["--target", "dev"], env={})
    assert "JOB_DATABASE_URL" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        rc.main(["--target", "dev"], env={"JOB_DATABASE_URL": "x"})
    assert "STORAGE_ENDPOINT" in capsys.readouterr().err
    only_supabase = {"JOB_DATABASE_URL": "x", "STORAGE_ENDPOINT": ENDPOINT, "STORAGE_BUCKET": "b",
                     "STORAGE_ACCESS_KEY_ID": "k", "STORAGE_SECRET_ACCESS_KEY": "s"}
    with pytest.raises(SystemExit):
        rc.main(["--target", "dev"], env=only_supabase)
    assert "R2_ENDPOINT" in capsys.readouterr().err


def test_main_refuses_the_same_bucket_for_both_sides(pair, capsys):
    src, _ = pair
    with pytest.raises(SystemExit):
        rc.main(["--target", "dev"], env={"JOB_DATABASE_URL": "x"}, src=src, dst=src)
    assert "same bucket" in capsys.readouterr().err


def test_main_copies_and_returns_the_exit_code(pair, monkeypatch, capsys):
    src, dst = pair
    r = put(src, HTML, "fed", "text/html", ".html")
    data = [(1, r.object_key, r.sha256, r.bytes)]

    @contextmanager
    def fake_connect(dsn, **kw):
        yield FakeConn(data)

    monkeypatch.setattr(rc, "connect", fake_connect)
    assert rc.main(["--target", "dev"], env={"JOB_DATABASE_URL": "x"}, src=src, dst=dst) == 0
    out = capsys.readouterr().out
    assert "copied 1, already there 0, mismatched 0" in out
    assert rc.main(["--target", "dev"], env={"JOB_DATABASE_URL": "x"}, src=src, dst=dst) == 0
    assert "copied 0, already there 1" in capsys.readouterr().out
    data[0] = (1, r.object_key, hashlib.sha256(b"x").digest(), r.bytes)
    assert rc.main(["--target", "dev"], env={"JOB_DATABASE_URL": "x"}, src=src, dst=dst) == 1


# ---------------------------------------------------------------- the backend setting

R2_ENV = {"R2_ENDPOINT": ENDPOINT, "R2_BUCKET": "r2b", "R2_ACCESS_KEY_ID": "rk", "R2_SECRET_ACCESS_KEY": "rs"}
SB_ENV = {"STORAGE_ENDPOINT": "http://supabase.invalid", "STORAGE_BUCKET": "raw", "STORAGE_ACCESS_KEY_ID": "sk",
          "STORAGE_SECRET_ACCESS_KEY": "ss", "STORAGE_REGION": "ap-south-1"}


def test_the_committed_config_keeps_both_targets_on_supabase_until_a_pull_request_flips_one():
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    assert set(cfg) == {"dev", "production"} and set(cfg.values()) <= set(BACKENDS)
    assert cfg == {"dev": "supabase", "production": "supabase"}            # flipped by the follow-up pull request (#164 step 3)


def test_with_no_r2_settings_and_no_target_supabase_storage_is_kept():
    s = Storage.from_env({**SB_ENV})
    assert s.bucket == "raw" and s._s3.meta.endpoint_url == "http://supabase.invalid"
    s = Storage.from_env({**SB_ENV, "FT_TARGET": "production"})            # a target set, but its line says supabase
    assert s.bucket == "raw"


def test_adding_r2_secrets_switches_nothing():
    s = Storage.from_env({**SB_ENV, **R2_ENV, "FT_TARGET": "dev"})
    assert s.bucket == "raw"


@pytest.mark.parametrize("target", ["dev", "production"])
def test_a_target_whose_line_says_r2_reads_the_r2_settings(tmp_path, monkeypatch, target):
    cfg = tmp_path / "storage.yaml"
    cfg.write_text(f"dev: supabase\nproduction: supabase\n{target}: r2\n")
    monkeypatch.setattr(storage_mod, "CONFIG_PATH", cfg)
    monkeypatch.setattr(storage_mod, "backend_for", lambda t, p=cfg: backend_for(t, p))
    s = Storage.from_env({**SB_ENV, **R2_ENV, "FT_TARGET": target})
    assert (s.bucket, s._s3.meta.endpoint_url, s._s3.meta.region_name) == ("r2b", ENDPOINT, "auto")
    other = "production" if target == "dev" else "dev"
    assert Storage.from_env({**SB_ENV, **R2_ENV, "FT_TARGET": other}).bucket == "raw"      # the other target is untouched
    with pytest.raises(KeyError):                                                           # R2 line, R2 settings missing
        Storage.from_env({**SB_ENV, "FT_TARGET": target})


def test_backend_for_is_strict_about_the_value_and_lenient_about_a_missing_line(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("dev: r2\n")
    assert backend_for("dev", p) == "r2" and backend_for("production", p) == "supabase"
    assert backend_for(None, p) == "supabase" and backend_for("staging", p) == "supabase"
    assert backend_for("dev", tmp_path / "missing.yaml") == "supabase"
    p.write_text("dev: b2\n")
    with pytest.raises(ValueError, match="must be one of"):
        backend_for("dev", p)


# ---------------------------------------------------------------- the workflow

def test_the_workflow_is_manual_takes_inputs_only_through_env_and_shares_backfills_group():
    wf = yaml.safe_load((WORKFLOWS / "r2-copy.yml").read_text())
    on = wf.get("on", wf.get(True))
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["target"]["default"] == "dev" and inputs["target"]["options"] == ["dev", "production"]
    assert inputs["dry_run"]["default"] is False
    assert wf["concurrency"]["group"] == yaml.safe_load((WORKFLOWS / "backfill.yml").read_text())["concurrency"]["group"]
    step = wf["jobs"]["run"]["steps"][-1]
    assert "inputs." not in step["run"] and "github.event.inputs" not in step["run"]
    assert not [k for k in step["env"] if k.startswith(("ANTHROPIC", "VOYAGE"))]        # no AI key


@pytest.mark.parametrize("inputs", [{}, {"target": "dev"}, {"target": ""}, {"target": "DEV"}])
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_the_workflow_reaches_production_only_when_chosen(inputs, dev_secrets):
    job_env, steps = resolve("r2-copy", "workflow_dispatch", inputs, secrets(dev_secrets))
    step = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert job_env["FT_TARGET"] == "dev"
    for n in ("JOB_DATABASE_URL", "STORAGE_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_SECRET_ACCESS_KEY"):
        assert step[n] == (f"DEV:{n}" if dev_secrets else "")                           # a missing DEV_ secret stays empty
    assert not [v for e in [job_env, *steps] for v in e.values() if "PROD:" in v]
    job_env, steps = resolve("r2-copy", "workflow_dispatch", {"target": "production"}, secrets(dev_secrets))
    step = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert all(step[n] == f"PROD:{n}" for n in ("JOB_DATABASE_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID"))
    assert not [v for e in [job_env, *steps] for v in e.values() if "DEV:" in v]


def test_the_integrity_error_is_still_the_storage_modules():
    assert rc.IntegrityError is IntegrityError
