"""Storage function against a local S3-protocol server (moto), standing in for Supabase Storage and R2."""
import gzip

import boto3
import pytest
from moto.server import ThreadedMotoServer

from ft.storage import IntegrityError, Storage

PORT = 5599
ENDPOINT = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
    for b in ("supabase-raw", "r2-raw"):
        s3.create_bucket(Bucket=b)
    yield s3
    srv.stop()


def store(bucket):
    return Storage(ENDPOINT, bucket, "test", "test", region="us-east-1")


HTML = ("<html><body>" + "The Committee decided to lower the target range. " * 400 + "</body></html>").encode()
PDF = b"%PDF-1.7\n" + bytes(range(256)) * 40


def test_text_is_gzipped_and_content_addressed(server):
    o = store("supabase-raw").put(HTML, source="fed", media_type="text/html", ext=".html")
    assert o.created and o.object_key.endswith(".html.gz")
    assert o.object_key.startswith(f"raw/fed/{o.sha256.hex()[:2]}/{o.sha256.hex()}")
    assert o.stored_bytes < o.bytes / 5                       # repetitive HTML compresses well
    assert store("supabase-raw").get(o.object_key) == HTML


def test_binary_is_stored_as_is(server):
    o = store("supabase-raw").put(PDF, source="rbi", media_type="application/pdf", ext=".pdf")
    assert o.object_key.endswith(".pdf") and o.stored_bytes == o.bytes
    assert store("supabase-raw").get(o.object_key) == PDF


def test_same_bytes_are_written_once(server):
    a = store("supabase-raw").put(HTML, source="fed", media_type="text/html", ext=".html")
    assert a.created is False                                  # stored by the first test
    keys = [k["Key"] for k in server.list_objects_v2(Bucket="supabase-raw")["Contents"]]
    assert keys.count(a.object_key) == 1


def test_gzip_is_deterministic(server):
    assert gzip.compress(HTML, 6, mtime=0) == gzip.compress(HTML, 6, mtime=0)


def test_tampered_object_fails_loudly(server):
    o = store("supabase-raw").put(b'{"rate": 5.25}', source="fred", media_type="application/json", ext=".json")
    server.put_object(Bucket="supabase-raw", Key=o.object_key, Body=gzip.compress(b'{"rate": 9.99}'))
    with pytest.raises(IntegrityError):
        store("supabase-raw").get(o.object_key)


def test_bad_source_key_rejected(server):
    with pytest.raises(ValueError):
        store("supabase-raw").put(b"x", source="../etc", media_type="text/plain", ext=".txt")


def test_ext_without_dot_is_normalised(server):
    o = store("supabase-raw").put(PDF + b"x", source="rbi", media_type="application/pdf", ext="pdf")
    assert o.object_key.endswith(f"{o.sha256.hex()}.pdf")
    assert store("supabase-raw").get(o.object_key) == PDF + b"x"          # readable, not an IntegrityError


@pytest.mark.parametrize("ext", ["", ".", ".PDF", ".tar.gz", ".htm/../x", ".toolongext"])
def test_bad_ext_rejected(server, ext):
    with pytest.raises(ValueError):
        store("supabase-raw").put(b"y", source="fed", media_type="text/plain", ext=ext)


def test_migration_keeps_keys_and_verifies(server):
    # remove the tampered object first: migration must refuse corrupt content
    src, dst = store("supabase-raw"), store("r2-raw")
    with pytest.raises(IntegrityError):
        src.copy_all_to(dst)
    for k in list(src.iter_keys()):
        if "/fred/" in k:
            server.delete_object(Bucket="supabase-raw", Key=k)
    copied, skipped = src.copy_all_to(dst)
    assert (copied + skipped) == len(list(src.iter_keys()))
    assert sorted(src.iter_keys()) == sorted(dst.iter_keys())   # same keys: core.raw_file needs no update
    for k in dst.iter_keys():
        assert dst.get(k) == src.get(k)
    assert dst.copy_all_to(src) == (0, len(list(src.iter_keys())))  # re-running copies nothing
