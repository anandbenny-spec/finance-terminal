"""Vercel entrypoint for the api service. Works whether or not the project itself is installed.

At import it writes one line to stderr, which Vercel keeps in the runtime logs: how long the import of the app took
and how old the process was when the app was ready. That's the cold start Arch 04 §6 item 2 asks to measure (A4,
24 Oct 2026); Vercel logs its own runtime dependency install just before it. Nothing here reaches a visitor."""
import os
import sys
import time
from pathlib import Path

_import_started = time.perf_counter()
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ft.api.app import app

__all__ = ["app"]


def process_age_s() -> float | None:
    """Seconds since this process started, from Linux's /proc (Vercel's runtime); None where there's no /proc."""
    try:
        after_name = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        start_ticks = int(after_name[19])          # field 22, starttime, in clock ticks since boot
        uptime_s = float(Path("/proc/uptime").read_text().split()[0])
        return round(uptime_s - start_ticks / os.sysconf("SC_CLK_TCK"), 2)
    except (OSError, ValueError, IndexError):
        return None


def cold_start_line(import_s: float, age_s: float | None) -> str:
    age = "unknown" if age_s is None else f"{age_s:.2f} s"
    return f"api cold start: app imported in {import_s:.2f} s; process age when ready {age}"


print(cold_start_line(time.perf_counter() - _import_started, process_age_s()), file=sys.stderr, flush=True)
