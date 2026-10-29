"""Copy every raw file from Supabase Storage to Cloudflare R2 and verify each one by hash (2B-01, #164; Arch 04 §1.1).

    python -m ft.jobs.r2_copy --target dev|production [--dry-run]

For each `core.raw_file` row, in id order:
1. read the object from Supabase Storage (`Storage.get` checks the bytes against the hash in the key); compare the
   original bytes with the row's `sha256` and `bytes`;
2. if R2 already holds the key, read it back the same way: the right hash means "already there" (nothing is written),
   the wrong one is a mismatch;
3. otherwise write it to R2 under the same key (same stored bytes, content type and metadata), read it back from R2 and
   compare its hash; a mismatch gets one retry (a fresh write), and a second one is reported.

It is resumable (a second run copies nothing and reads everything back) and prints counts: objects, bytes, copied,
already there, mismatched. A mismatch fails the run (exit 1) and names the object. It never deletes on either side,
and it never overwrites an R2 object whose hash is wrong: that needs a look from Anand (the card's stop condition).
It reads the database as `job_writer` (JOB_DATABASE_URL, read only), the Supabase side from STORAGE_* and R2 from R2_*
(the workflow reads the target's own secrets). It does not switch anything: the switch is a line in
`py/config/storage.yaml`, flipped in a pull request once this run shows 0 mismatches.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import sys
import zlib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

from ft.jobs.runner import connect
from ft.storage import IntegrityError, Storage

PAGE = 500            # raw_file rows read per query
WORKERS = 8           # parallel objects (boto3 clients are thread-safe)
READ_ERRORS = (ClientError, BotoCoreError, OSError, EOFError, zlib.error)     # a missing, unreadable or non-gzip object
LISTED = 10           # mismatched objects named in the output


@dataclass
class Row:
    object_key: str
    sha256: bytes
    bytes: int


@dataclass
class Outcome:
    status: str            # copied | there | mismatch
    key: str
    nbytes: int = 0        # stored (compressed) size of the object
    reason: str = ""


@dataclass
class Counts:
    objects: int = 0
    bytes: int = 0
    copied: int = 0
    there: int = 0
    mismatched: list[Outcome] = field(default_factory=list)


def rows(conn, page: int = PAGE) -> Iterator[Row]:
    last = 0
    while True:
        batch = conn.execute("select id, object_key, sha256, bytes from core.raw_file where id > %s order by id limit %s",
                             (last, page)).fetchall()
        if not batch:
            return
        for id_, key, sha, nbytes in batch:
            yield Row(key, bytes(sha), nbytes)
        last = batch[-1][0]


def _check_original(row: Row, data: bytes) -> str:
    if hashlib.sha256(data).digest() != row.sha256:
        return "content does not match the hash in core.raw_file"
    if len(data) != row.bytes:
        return f"size {len(data)} does not match core.raw_file's {row.bytes}"
    return ""


def _read_back(storage: Storage, row: Row) -> str:
    """'' if the object reads back with the row's hash and size, else why not."""
    try:
        return _check_original(row, storage.get(row.object_key))
    except IntegrityError:
        return "content does not match the hash in its key"
    except READ_ERRORS as e:                                           # missing, unreadable, not gzip
        return f"unreadable: {type(e).__name__}"


def copy_one(row: Row, src: Storage, dst: Storage, *, dry_run: bool = False) -> Outcome:
    """Copy and verify one object. Never deletes, never overwrites an R2 object that exists."""
    key = row.object_key
    try:
        body = src._s3.get_object(Bucket=src.bucket, Key=key)
    except READ_ERRORS as e:
        return Outcome("mismatch", key, reason=f"Supabase: unreadable: {type(e).__name__}")
    raw = body["Body"].read()
    try:
        Storage.get_verify_bytes(key, raw)
        bad = _check_original(row, gzip.decompress(raw) if key.endswith(".gz") else raw)
    except IntegrityError:
        bad = "Supabase copy does not match the hash in its key"
    except READ_ERRORS as e:
        bad = f"Supabase: unreadable: {type(e).__name__}"
    if bad:
        return Outcome("mismatch", key, len(raw), f"Supabase copy: {bad}")
    if dst._exists(key):
        bad = _read_back(dst, row)
        return Outcome("there", key, len(raw)) if not bad else Outcome("mismatch", key, len(raw), f"R2 copy: {bad}")
    if dry_run:
        return Outcome("copied", key, len(raw))
    for attempt in (1, 2):
        dst._s3.put_object(Bucket=dst.bucket, Key=key, Body=raw,
                           ContentType=body.get("ContentType", "application/octet-stream"),
                           Metadata=body.get("Metadata", {}))
        bad = _read_back(dst, row)
        if not bad:
            return Outcome("copied", key, len(raw))
    return Outcome("mismatch", key, len(raw), f"after a retry, R2 copy: {bad}")


def copy_all(source_rows: Iterator[Row], src: Storage, dst: Storage, *, dry_run: bool = False,
             workers: int = WORKERS) -> Counts:
    counts = Counts()
    with ThreadPoolExecutor(workers) as pool:
        for o in pool.map(lambda r: copy_one(r, src, dst, dry_run=dry_run), source_rows):
            counts.objects += 1
            counts.bytes += o.nbytes
            if o.status == "copied":
                counts.copied += 1
            elif o.status == "there":
                counts.there += 1
            else:
                counts.mismatched.append(o)
    return counts


def report(c: Counts, target: str, dry_run: bool, out: Callable[[str], None] = print) -> bool:
    ok = not c.mismatched
    verb = "would copy" if dry_run else "copied"
    out(f"{'::notice::' if ok else '::error::'}r2 copy ({target}{', dry run' if dry_run else ''}): objects {c.objects}, "
        f"bytes {c.bytes}, {verb} {c.copied}, already there {c.there}, mismatched {len(c.mismatched)}")
    for o in c.mismatched[:LISTED]:
        out(f"::error::r2 copy mismatch: {o.key}: {o.reason}")
    if len(c.mismatched) > LISTED:
        out(f"::error::r2 copy: {len(c.mismatched) - LISTED} more mismatches not listed")
    out("::notice::passed" if ok else "::error::failed")
    return ok


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ft.jobs.r2_copy", description=__doc__.split("\n")[0])
    p.add_argument("--target", choices=("dev", "production"), required=True)
    p.add_argument("--dry-run", action="store_true", help="read and compare everything, write nothing to R2")
    return p


def main(argv: list[str] | None = None, *, env=None, src: Storage | None = None, dst: Storage | None = None) -> int:
    env = os.environ if env is None else env
    p = parser()
    args = p.parse_args(argv)
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")
    try:
        src = src or Storage.supabase_from_env(env)
        dst = dst or Storage.r2_from_env(env)
    except KeyError as e:
        p.error(f"{e.args[0]} is not set (the copy needs both the STORAGE_* and the R2_* settings of the target)")
    if (src.bucket, src._s3.meta.endpoint_url) == (dst.bucket, dst._s3.meta.endpoint_url):
        p.error("the source and the destination are the same bucket")
    print(f"r2 copy: target {args.target}" + (" (dry run)" if args.dry_run else ""))
    with connect(dsn, autocommit=True, role="check") as conn:
        counts = copy_all(rows(conn), src, dst, dry_run=args.dry_run)
    return 0 if report(counts, args.target, args.dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
