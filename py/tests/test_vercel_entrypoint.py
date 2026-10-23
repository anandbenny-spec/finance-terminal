"""The deployment shape Vercel Services reads (A4, F18; Arch 04 §1.2 and §6 items 1-2).

Checked on the first deploys (24 Oct 2026): `/api/health` answered from the api service with Vercel passing the full
`/api/...` path, so `app.py` keeps its `/api` prefix. These tests keep that shape from drifting: the entrypoint
`main:app` in both files, `/api/*` routed to the api service ahead of the web catch-all, the app answering on the full
path, the cold-start line in the runtime logs, and the api service's base dependencies holding no `jobs` package
and none of the heavy packages the voyageai SDK brought (24 Oct 2026, issue #9: Vercel installed them at every cold
start)."""
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PY = Path(__file__).resolve().parents[1]
ROOT = PY.parent
VERCEL = json.loads((ROOT / "vercel.json").read_text())
PYPROJECT = tomllib.loads((PY / "pyproject.toml").read_text())


def _main():
    spec = importlib.util.spec_from_file_location("ft_vercel_main", PY / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service_for(path: str) -> str:
    """The first rewrite whose source matches the whole path wins, as Vercel applies them."""
    for rule in VERCEL["rewrites"]:
        if re.fullmatch(rule["source"], path):
            return rule["destination"]["service"]
    raise AssertionError(f"no rewrite matches {path}")


def _name(requirement: str) -> str:
    return re.split(r"[\s\[<>=!~;]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def test_the_entrypoint_is_main_app_in_vercel_json_and_pyproject():
    services = VERCEL["services"]
    assert services["api"] == {"root": "py/", "entrypoint": "main:app"}
    assert services["web"]["root"] == "web/"
    assert PYPROJECT["tool"]["vercel"]["entrypoint"] == "main:app"
    assert (PY / "main.py").is_file()


@pytest.mark.parametrize("path, service", [
    ("/api/health", "api"),
    ("/api/admin/refresh", "api"),
    ("/", "web"),
    ("/policy", "web"),
    ("/policy/fed/2026-10-28", "web"),
    ("/apix", "web"),
])
def test_api_paths_go_to_the_api_service_and_everything_else_to_web(path, service):
    assert _service_for(path) == service


def test_the_app_answers_health_on_the_full_api_path():
    client = TestClient(_main().app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["content-type"] == "application/json"
    # Vercel passes the full path (seen on the first deploy), so nothing is served without the prefix.
    assert client.get("/health").status_code == 404
    missing = client.get("/api/not-a-route")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}


def test_importing_main_writes_one_cold_start_line_to_stderr():
    done = subprocess.run([sys.executable, "-c", "import main"], cwd=PY, capture_output=True, text=True,
                          timeout=60, check=True)
    lines = [line for line in done.stderr.splitlines() if line.startswith("api cold start:")]
    assert len(lines) == 1, done.stderr
    assert re.fullmatch(r"api cold start: app imported in \d+\.\d\d s; process age when ready "
                        r"(\d+\.\d\d s|unknown)", lines[0])
    assert done.stdout == ""


def test_the_cold_start_line_reads_both_numbers():
    main = _main()
    assert main.cold_start_line(0.123, 1.5) == "api cold start: app imported in 0.12 s; process age when ready 1.50 s"
    assert main.cold_start_line(0.004, None).endswith("process age when ready unknown")


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="process age is read from Linux's /proc")
def test_process_age_is_this_processes_age_on_linux():
    age = _main().process_age_s()
    assert age is not None and 0 <= age < 24 * 3600


def test_the_api_service_installs_no_jobs_or_eval_package():
    """Vercel installs only the base dependencies, so fetch and parse libraries stay out of them (CLAUDE.md §2)."""
    base = {_name(r) for r in PYPROJECT["project"]["dependencies"]}
    extras = PYPROJECT["project"]["optional-dependencies"]
    for extra in ("jobs", "eval"):
        assert not base & {_name(r) for r in extras[extra]}, extra


# What the voyageai SDK pulled into the api service (36 packages, about 152 MB; issue #9), and the jobs' heavy ones.
HEAVY = {"voyageai", "langchain-core", "langsmith", "huggingface-hub", "tokenizers", "hf-xet", "numpy", "pillow",
         "aiohttp", "pandas", "lxml", "yfinance", "curl-cffi"}


def _base_closure() -> set[str]:
    """Every package Vercel installs for the api service: the base dependencies' closure in uv.lock."""
    lock = {p["name"]: p for p in tomllib.loads((PY / "uv.lock").read_text())["package"]}
    seen, todo = set(), [(d["name"], tuple(d.get("extra", ()))) for d in lock["finance-terminal"]["dependencies"]]
    while todo:
        name, extras = todo.pop()
        key = (name, extras)
        if key in seen:
            continue
        seen.add(key)
        pkg = lock[name]
        deps = list(pkg.get("dependencies", []))
        for extra in extras:
            deps += pkg.get("optional-dependencies", {}).get(extra, [])
        todo += [(d["name"], tuple(d.get("extra", ()))) for d in deps]
    return {name for name, _ in seen}


def test_the_api_service_bundle_holds_none_of_the_heavy_packages():
    closure = _base_closure()
    assert {"anthropic", "fastapi", "httpx", "psycopg-binary", "pglast", "boto3"} <= closure
    assert not closure & HEAVY, sorted(closure & HEAVY)
