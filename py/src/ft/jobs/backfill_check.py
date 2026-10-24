"""The backfill re-run check (Prompt 01 B8, part 1; Architecture 02 §1.8; Architecture 01 §1.5; Architecture 04 §1.1).

B8's done-when: "Re-running the backfill writes no new raw objects, document versions or AI requests; every raw file
reads back hash-verified." Part 1 covers the four data tasks, on a project where they're already loaded:

    python -m ft.jobs.backfill_check --target dev [--tasks policy.calendar,policy.market_series,...]
                                     [--since 2019-01-01] [--until YYYY-MM-DD] [--verify-only]

1. For each task in turn: count what a run could add (below), run the task's backfill exactly as `backfill.yml` does
   (`ft.jobs.run --workflow backfill`, the same code path and arguments), count again, and list what's new.
2. Read back every `core.raw_file` object through the storage function: `Storage.get` checks the bytes against the
   hash in the object's key, and this checks them against the row's `sha256` and `bytes` too.

It fails (exit 1) if a task's run failed, if a run added a document (every version is a row of `core.document`) or an
AI request, if it added a raw file or a stored object other than those below, or if any raw file doesn't read back.
Everything else it counts (chunks, diffs, observations, revisions) is reported, not judged: a series backfill that runs
to today adds the days since the last load, and re-chunking is allowed.

Sources whose bytes change on every fetch (decided by Anand, 24 Oct 2026, option (a) on #17): the first dev check found
that a re-run stored new raw files only for responses that differ each time while the data doesn't. FRED's JSON
carries the request date, Yahoo's chart JSON live market metadata, and RBI's current-year page per-request ASP.NET
state. Those three (VOLATILE, matched by source and URL) may add raw files and stored objects, one object per raw file;
each is reported. Anything else new still fails, and so does an object stored without its raw-file row.

It connects as `job_writer` (JOB_DATABASE_URL), reads only, and uses the storage keys the jobs use. The workflow
(`backfill-check.yml`) prints its results as annotations, since Claude's workspace can't download the run's log.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

from ft.jobs.runner import connect
from ft.storage import IntegrityError, Storage

TASKS = ("policy.calendar", "policy.market_series", "policy.fed_documents", "policy.rbi_documents")

# What a re-run must not add (B8's done-when), and what is only reported.
BLOCKING = ("raw_files", "stored_objects", "documents", "ai_requests")
COUNTS = {
    "raw_files": "select count(*) from core.raw_file",
    "documents": "select count(*) from core.document",
    "ai_requests": "select count(*) from ops.ai_request",
    "document_diffs": "select count(*) from core.document_diff",
    "chunks": "select count(*) from core.chunk",
    "observations": "select count(*) from core.observation",
    "observation_revisions": "select count(*) from core.observation_revision",
}
# (source key, URL) whose bytes change on every fetch: a prefix ends in '?' or '/', anything else matches exactly
# (RBI's earlier fiscal years, recorded as the page's URL plus '#hdnYear=…', are closed and must not change).
VOLATILE = (
    ("fred", "https://api.stlouisfed.org/fred/series/observations?"),
    ("yfinance", "https://query2.finance.yahoo.com/v8/finance/chart/"),
    ("rbi", "https://www.rbi.org.in/Scripts/Annualpolicy.aspx"),
)
LISTED = 10          # new rows listed per kind (the rest are counted)
WORKERS = 8          # parallel reads in the verification (boto3 clients are thread-safe)


@dataclass
class Snapshot:
    counts: dict[str, int | None]
    max_raw_file_id: int
    max_document_id: int


@dataclass
class TaskResult:
    task: str
    exit_code: int
    delta: dict[str, int | None]
    new_raw_files: list[str] = field(default_factory=list)      # "source: url (n bytes)", the first LISTED not volatile
    new_documents: list[str] = field(default_factory=list)      # "doc_type external_key v<n>"
    volatile: int = 0                                           # new raw files from VOLATILE sources
    volatile_listed: list[str] = field(default_factory=list)

    @property
    def grew(self) -> list[str]:
        allowed = {"raw_files": self.volatile, "stored_objects": self.volatile}
        return [k for k in BLOCKING if (self.delta.get(k) or 0) > allowed.get(k, 0)]

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.grew


def count_objects(storage: Storage | None) -> int | None:
    """Objects under the storage prefix, or None if the keys can't list (the count is then not compared)."""
    if storage is None:
        return None
    try:
        return sum(1 for _ in storage.iter_keys())
    except Exception as e:  # noqa: BLE001 - a key without list permission: the raw_file count still covers it
        print(f"::warning title=backfill check::stored objects not counted ({type(e).__name__})")
        return None


def snapshot(conn, storage: Storage | None) -> Snapshot:
    counts: dict[str, int | None] = {k: conn.execute(q).fetchone()[0] for k, q in COUNTS.items()}
    counts["stored_objects"] = count_objects(storage)
    raw = conn.execute("select coalesce(max(id), 0) from core.raw_file").fetchone()[0]
    doc = conn.execute("select coalesce(max(id), 0) from core.document").fetchone()[0]
    return Snapshot(counts, raw, doc)


def delta(before: Snapshot, after: Snapshot) -> dict[str, int | None]:
    out = {}
    for k, b in before.counts.items():
        a = after.counts.get(k)
        out[k] = None if a is None or b is None else a - b
    return out


def is_volatile(source: str, url: str | None) -> bool:
    return url is not None and any(source == s and (url.startswith(u) if u[-1] in "?/" else url == u)
                                   for s, u in VOLATILE)


def new_rows(conn, before: Snapshot) -> tuple[list[str], list[str], int, list[str]]:
    """The new raw files not from a volatile source (the first LISTED), the new documents, and the volatile ones
    (counted, the first LISTED listed)."""
    raws, volatile, n_volatile = [], [], 0
    for s, u, key, n in conn.execute(
            """select s.key, f.source_url, f.object_key, f.bytes from core.raw_file f
                 join core.source s on s.id = f.source_id where f.id > %s order by f.id""", (before.max_raw_file_id,)):
        line = f"{s}: {u or key} ({n} bytes)"
        if is_volatile(s, u):
            n_volatile += 1
            if len(volatile) < LISTED:
                volatile.append(line)
        elif len(raws) < LISTED:
            raws.append(line)
    docs = [f"{t} {k} v{v}" for t, k, v in conn.execute(
        """select doc_type, external_key, version_no from core.document where id > %s order by id limit %s""",
        (before.max_document_id, LISTED))]
    return raws, docs, n_volatile, volatile


def run_backfill(task: str, args: argparse.Namespace, env) -> int:
    """The task's backfill, as backfill.yml runs it (same entry point and arguments)."""
    from ft.jobs import run
    argv = ["--workflow", "backfill", "--task", task, "--target", args.target, "--runner", args.runner]
    if args.since:
        argv += ["--since", args.since.isoformat()]
    if args.until:
        argv += ["--until", args.until.isoformat()]
    if args.github_run_id is not None:
        argv += ["--github-run-id", str(args.github_run_id)]
    if args.git_sha:
        argv += ["--git-sha", args.git_sha]
    try:
        return run.main(argv, env=env)
    except SystemExit as e:                     # argparse refused the arguments
        return e.code if isinstance(e.code, int) else 2


def verify(conn, storage: Storage, workers: int = WORKERS, after_id: int = 0) -> tuple[int, list[str]]:
    """Reads back every raw file (the check always passes after_id 0: all of them; the tests narrow it to their own
    rows of the shared test database). Returns how many were checked and the failures ("object_key: why")."""
    rows = conn.execute("select object_key, sha256, bytes from core.raw_file where id > %s order by id",
                        (after_id,)).fetchall()

    def one(row) -> str | None:
        key, sha, size = row
        try:
            data = storage.get(key)                     # checks the bytes against the hash in the key
        except IntegrityError:
            return f"{key}: content does not match its hash"
        except Exception as e:  # noqa: BLE001 - a missing object or a read error is a failure, never a skip
            return f"{key}: not readable ({type(e).__name__})"
        if key.rsplit("/", 1)[-1].split(".", 1)[0] != bytes(sha).hex():
            return f"{key}: the row's sha256 differs from the key's"
        if len(data) != size:
            return f"{key}: {len(data)} bytes read, the row says {size}"
        return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        failures = [f for f in pool.map(one, rows) if f]
    return len(rows), failures


def _date(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def _tasks(text: str) -> list[str]:
    tasks = [t.strip() for t in text.split(",") if t.strip()]
    if not tasks:
        raise argparse.ArgumentTypeError("no task given")
    return tasks


def _int(text: str) -> int | None:
    return int(text) if text.strip() else None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ft.jobs.backfill_check", description=__doc__.split("\n\n")[0])
    p.add_argument("--tasks", type=_tasks, default=list(TASKS), help="comma-separated task keys (default: the four)")
    p.add_argument("--since", type=_date, default=date(2019, 1, 1))
    p.add_argument("--until", type=_date, default=None, help="blank = today")
    p.add_argument("--target", choices=("dev", "production"), default="dev")
    p.add_argument("--verify-only", action="store_true", help="only read back every raw file")
    p.add_argument("--runner", choices=("github", "self_hosted", "local"), default="local")
    p.add_argument("--github-run-id", type=_int)
    p.add_argument("--git-sha")
    return p


def _say(level: str, text: str, out: Callable[[str], None]) -> None:
    out(f"::{level} title=backfill check::{text}")


def report(results: Iterable[TaskResult], checked: int | None, failures: list[str],
           out: Callable[[str], None] = print) -> bool:
    ok = True
    for r in results:
        shown = ", ".join(f"{k} {'+' if (v or 0) >= 0 else ''}{v if v is not None else 'not counted'}"
                          for k, v in r.delta.items())
        _say("notice" if r.ok else "error", f"{r.task}: exit {r.exit_code}; {shown}"
             + (f"; of the raw files, {r.volatile} from sources whose bytes change on every fetch" if r.volatile else ""), out)
        if r.volatile:
            _say("notice", f"{r.task} new raw files from changing sources: " + "; ".join(r.volatile_listed), out)
        if r.grew:
            ok = False
            if r.new_raw_files:
                _say("error", f"{r.task} new raw files: " + "; ".join(r.new_raw_files), out)
            if r.new_documents:
                _say("error", f"{r.task} new documents: " + "; ".join(r.new_documents), out)
        if r.exit_code != 0:
            ok = False
    if checked is not None:
        if failures:
            ok = False
            _say("error", f"raw files: {len(failures)} of {checked} failed to read back: " + "; ".join(failures[:LISTED]), out)
        else:
            _say("notice", f"raw files: all {checked} read back hash-verified", out)
    _say("notice" if ok else "error", "passed" if ok else "failed", out)
    return ok


def main(argv: list[str] | None = None, *, env=None, storage: Storage | None = None,
         backfill: Callable[[str, argparse.Namespace, dict], int] = run_backfill, verify_after_id: int = 0) -> int:
    env = os.environ if env is None else env
    p = parser()
    args = p.parse_args(argv)
    if args.since and args.until and args.since > args.until:
        p.error("--since is after --until")
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")
    if storage is None:
        try:
            storage = Storage.from_env()
        except KeyError as e:
            p.error(f"{e.args[0]} is not set (the storage keys are needed to read the raw files back)")
    print(f"backfill check: target {args.target}, "
          + ("verify only" if args.verify_only else f"tasks {', '.join(args.tasks)}, since {args.since}, until {args.until or 'today'}"))

    results: list[TaskResult] = []
    with connect(dsn, autocommit=True, role="check") as conn:
        if not args.verify_only:
            for task in args.tasks:
                before = snapshot(conn, storage)
                code = backfill(task, args, env)
                after = snapshot(conn, storage)
                raws, docs, n_volatile, volatile = new_rows(conn, before)
                results.append(TaskResult(task, code, delta(before, after), raws, docs, n_volatile, volatile))
        checked, failures = verify(conn, storage, after_id=verify_after_id)
    return 0 if report(results, checked, failures) else 1


if __name__ == "__main__":
    sys.exit(main())
