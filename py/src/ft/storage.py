"""The one storage function (brief §4.2). Every raw-file read and write goes through here.

- One S3-protocol client for every backend: Supabase Storage now, Cloudflare R2 (or Backblaze B2) later.
  Switching is five settings plus a copy of the objects; object keys don't change, so `core.raw_file`
  needs no update.
- Keys are content-addressed: raw/<source>/<first 2 hex of sha256>/<sha256><ext>[.gz]. The same bytes
  always get the same key, so a re-fetch never writes twice and an object can't be silently replaced.
- Text-like files (HTML, JSON, CSV, XML, plain text) are gzipped before upload (typically 5-10x smaller);
  PDFs, zips and images are stored as they are. Gzip output is deterministic (no timestamp in the header).
- Every read is checked against the hash in its key, so corruption or tampering fails loudly.
"""
from __future__ import annotations

import gzip
import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import boto3
import yaml
from botocore.config import Config
from botocore.exceptions import ClientError

TEXT_TYPES = ("text/", "application/json", "application/xml", "application/rss+xml", "application/xhtml+xml")


BACKENDS = ("supabase", "r2")
TARGETS = ("dev", "production")
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "storage.yaml"


class IntegrityError(Exception):
    pass


def backend_for(target: str | None, config_path: Path = CONFIG_PATH) -> str:
    """The backend a target uses, from the committed config (py/config/storage.yaml). No target, an unknown target or a
    missing config line means Supabase Storage, so R2 is used only where a line says so. A line with any other
    value is an error, never a quiet fallback."""
    if target not in TARGETS:
        return "supabase"
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    except FileNotFoundError:
        return "supabase"
    backend = cfg.get(target, "supabase")
    if backend not in BACKENDS:
        raise ValueError(f"{config_path.name}: {target} must be one of {', '.join(BACKENDS)}, not {backend!r}")
    return backend


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
    def from_env(cls, env: Mapping[str, str] | None = None, *, target: str | None = None,
                 backend: str | None = None) -> Storage:
        """The target's backend (FT_TARGET unless given; the job workflows set it), as the committed config says:
        the STORAGE_* settings for Supabase Storage, the R2_* settings for R2. With no FT_TARGET it is Supabase."""
        e = os.environ if env is None else env
        backend = backend or backend_for(target or e.get("FT_TARGET"))
        if backend == "r2":
            return cls.r2_from_env(e)
        return cls.supabase_from_env(e)

    @classmethod
    def supabase_from_env(cls, e: Mapping[str, str] | None = None) -> Storage:
        e = os.environ if e is None else e
        return cls(e["STORAGE_ENDPOINT"], e["STORAGE_BUCKET"], e["STORAGE_ACCESS_KEY_ID"],
                   e["STORAGE_SECRET_ACCESS_KEY"], e.get("STORAGE_REGION", "auto"))

    @classmethod
    def r2_from_env(cls, e: Mapping[str, str] | None = None) -> Storage:
        e = os.environ if e is None else e
        return cls(e["R2_ENDPOINT"], e["R2_BUCKET"], e["R2_ACCESS_KEY_ID"], e["R2_SECRET_ACCESS_KEY"], "auto")

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
