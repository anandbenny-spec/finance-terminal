"""The one storage function (brief §4.2). Every raw-file read and write goes through here.

- One S3-protocol client for every backend: Supabase Storage now, Cloudflare R2 (or Backblaze B2) later.
  Switching is five settings plus a copy of the objects; object keys don't change, so `core.raw_file`
  needs no update.
- Keys are content-addressed: raw/<source>/<first 2 hex of sha256>/<sha256><ext>[.gz]. The same bytes
  always get the same key, so a re-fetch never writes twice and an object can't be silently replaced.
- Text-like files (HTML, JSON, CSV, XML, plain text) are gzipped before upload (typically 5-10x smaller);
  PDFs, zips and images are stored as they are. Gzip output is deterministic (no timestamp in the header).
- Every read is checked against the hash in its key, so corruption or tampering fails loudly.
- `StorageStop` (F3-02, #387) answers "may this task store a new file now?" for the tasks that opt in; `put` never asks it.
"""
from __future__ import annotations

import gzip
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

TEXT_TYPES = ("text/", "application/json", "application/xml", "application/rss+xml", "application/xhtml+xml")


class IntegrityError(Exception):
    pass


def _norm_ext(ext: str) -> str:
    """'pdf' and '.pdf' both give '.pdf'. get() reads the hash as the file name up to the first dot, so an
    extension without its dot would make every later read fail (Review 01, F16)."""
    bare = ext.lstrip(".")
    if not (1 <= len(bare) <= 8 and bare.isascii() and bare.isalnum() and bare == bare.lower()):
        raise ValueError(f"ext must be 1-8 lower-case letters or digits, with or without a leading dot: {ext!r}")
    return "." + bare


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    sha256: bytes          # of the original bytes (what core.raw_file.sha256 holds)
    bytes: int             # original size
    stored_bytes: int      # size after compression
    media_type: str
    created: bool          # False if the object already existed


class Storage:
    def __init__(self, endpoint_url: str, bucket: str, access_key_id: str, secret_access_key: str,
                 region: str = "auto", prefix: str = "raw"):
        self.bucket, self.prefix = bucket, prefix
        self._s3 = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region,
            aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 5, "mode": "standard"}))

    @classmethod
    def from_env(cls) -> Storage:
        e = os.environ
        return cls(e["STORAGE_ENDPOINT"], e["STORAGE_BUCKET"], e["STORAGE_ACCESS_KEY_ID"],
                   e["STORAGE_SECRET_ACCESS_KEY"], e.get("STORAGE_REGION", "auto"))

    # ------------------------------------------------------------------ write
    def put(self, data: bytes, *, source: str, media_type: str, ext: str) -> StoredObject:
        if not source.replace("_", "").isalnum() or not source.islower():
            raise ValueError(f"source key must be lower-case letters, digits and '_': {source!r}")
        ext = _norm_ext(ext)
        digest = hashlib.sha256(data).digest()
        hexd = digest.hex()
        compress = media_type.startswith(TEXT_TYPES)
        payload = gzip.compress(data, compresslevel=6, mtime=0) if compress else data
        key = f"{self.prefix}/{source}/{hexd[:2]}/{hexd}{ext}{'.gz' if compress else ''}"
        if self._exists(key):
            return StoredObject(key, digest, len(data), len(payload), media_type, created=False)
        self._s3.put_object(
            Bucket=self.bucket, Key=key, Body=payload,
            ContentType="application/gzip" if compress else media_type,
            Metadata={"sha256": hexd, "media-type": media_type})
        return StoredObject(key, digest, len(data), len(payload), media_type, created=True)

    def stored_bytes(self) -> tuple[int, int]:
        """(objects, bytes) stored under this storage's prefix, as the object store reports them: compressed text counts at
        its compressed size, which is what a plan's storage quota counts (`core.raw_file.bytes` holds source sizes)."""
        n = size = 0
        for page in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=f"{self.prefix}/"):
            for o in page.get("Contents", []):
                n, size = n + 1, size + o["Size"]
        return n, size

    # ------------------------------------------------------------------ read
    def get(self, object_key: str) -> bytes:
        body = self._s3.get_object(Bucket=self.bucket, Key=object_key)["Body"].read()
        data = gzip.decompress(body) if object_key.endswith(".gz") else body
        expected = object_key.rsplit("/", 1)[-1].split(".", 1)[0]
        if hashlib.sha256(data).hexdigest() != expected:
            raise IntegrityError(f"{object_key}: content does not match its hash")
        return data

    def _exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    # ------------------------------------------------------------------ migration (Supabase Storage -> R2)
    def iter_keys(self):
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix + "/"}
            if token:
                kw["ContinuationToken"] = token
            page = self._s3.list_objects_v2(**kw)
            for obj in page.get("Contents", []):
                yield obj["Key"]
            if not page.get("IsTruncated"):
                return
            token = page["NextContinuationToken"]

    def copy_all_to(self, target: Storage) -> tuple[int, int]:
        """Copy every object to another backend under the same key, verifying each on the way."""
        copied = skipped = 0
        for key in self.iter_keys():
            if target._exists(key):
                skipped += 1
                continue
            raw = self._s3.get_object(Bucket=self.bucket, Key=key)
            body = raw["Body"].read()
            self.get_verify_bytes(key, body)
            target._s3.put_object(Bucket=target.bucket, Key=key, Body=body,
                                  ContentType=raw.get("ContentType", "application/octet-stream"),
                                  Metadata=raw.get("Metadata", {}))
            copied += 1
        return copied, skipped

    @staticmethod
    def get_verify_bytes(key: str, body: bytes) -> None:
        data = gzip.decompress(body) if key.endswith(".gz") else body
        if hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[-1].split(".", 1)[0]:
            raise IntegrityError(f"{key}: content does not match its hash")


# ---------------------------------------------------------------------- the storage stop (F3-02, #387)
MB = 1024 * 1024                        # the unit of the stops in config (the raw_storage check's too)


class StorageStop:
    """F3-02 (#387, 11 Nov 2026): may a growth task store a new raw file now?

    **Opt-in.** Nothing in this module consults it: `Storage.put` stores whatever it is given, as before. A task that grows the
    bucket by whole documents (`equity.call_transcripts`; `equity.brsr` when it exists) constructs one at the start of its run,
    with its stop, and asks `allows()` before it fetches a new file. Module 1's document tasks and every other task never
    construct one, so they store up to Supabase's own cap: the stop exists to keep that room for them.

    The bucket is listed once (`Storage.stored_bytes()`: an object listing, no download, so no egress), and each new file the
    task stores is added with `add()`, so the answer stays current through the run without listing again. A bucket that can't
    be listed holds new files: the stop fails closed, with its own message."""

    def __init__(self, storage: Storage | Callable[[], Storage] | None, stop_mb: int):
        if stop_mb <= 0:
            raise ValueError("stop_mb must be positive")
        self.stop_mb = stop_mb
        self.at_start: int | None = None                       # the bucket's stored bytes when the run began; None: unreadable
        self.added = 0                                         # bytes this run stored since
        try:
            st = storage() if callable(storage) else storage
            if st is not None:
                self.at_start = int(st.stored_bytes()[1])
        except Exception:                                      # noqa: BLE001 - any failure to list means "unknown": hold
            self.at_start = None

    @property
    def readable(self) -> bool:
        return self.at_start is not None

    @property
    def stored(self) -> int | None:
        """The bucket's stored bytes now, as far as this run knows (the listing plus what it added)."""
        return None if self.at_start is None else self.at_start + self.added

    def allows(self) -> bool:
        """True while the bucket is readable and under the stop; at or over it, or unreadable, new files are held."""
        return self.stored is not None and self.stored < self.stop_mb * MB

    def add(self, nbytes: int) -> None:
        """Count a file this run stored (its stored size: a PDF's is its source size)."""
        self.added += max(0, int(nbytes))

    def describe(self) -> str:
        """One line for a check or an alert: sizes and the stop, never names or contents."""
        if self.at_start is None:
            return (f"the raw bucket's size couldn't be read, so new files are held (the stop fails closed; "
                    f"read Storage in the dashboard; the stop is {self.stop_mb} MB)")
        return (f"the raw bucket holds {self.stored / MB:.1f} MB of stored objects, "
                f"{'under' if self.allows() else 'at or over'} the {self.stop_mb} MB stop for new files")
