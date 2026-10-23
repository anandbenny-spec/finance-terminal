"""Starts `refresh.yml` through GitHub's API (Architecture 02 §1.6 step 3).

The token is `GITHUB_DISPATCH_TOKEN`: fine-grained, Actions: write on this one repo, with an expiry, held only in
Vercel. Inputs match `refresh.yml`'s `workflow_dispatch` inputs; `target` is the project this deployment runs
against (issue #2 item 1): Vercel's production deployment dispatches with `production`, every other deployment
(preview, development, a local run) with `dev`, so nothing but the production deployment can start a production run.
`return_run_details` asks GitHub to answer with the new run's id (200); if it answers 204 instead, the request is
still recorded as dispatched, without a run id (Architecture 02 §7 item 3 checks this on the real API).
"""
from __future__ import annotations

import os

import httpx

REPO = "anandbenny-spec/finance-terminal"
WORKFLOW = "refresh.yml"
REF = "main"
TIMEOUT_S = 10.0


class DispatchError(Exception):
    """The workflow wasn't started. The message is for the server log, never for a visitor."""


def deployment_target() -> str:
    return "production" if os.environ.get("VERCEL_ENV") == "production" else "dev"


def dispatch_refresh(client: httpx.Client, token: str | None, task_key: str, request_id: int,
                     target: str) -> int | None:
    """Starts the run and returns its id when GitHub returns one."""
    if not token:
        raise DispatchError("GITHUB_DISPATCH_TOKEN is not set")
    if target not in ("dev", "production"):
        raise DispatchError(f"unknown target {target!r}")
    try:
        resp = client.post(
            f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"ref": REF, "return_run_details": True,
                  "inputs": {"task": task_key, "request_id": str(request_id), "trigger": "admin", "target": target}},
            timeout=TIMEOUT_S)
    except httpx.HTTPError as e:
        raise DispatchError(f"GitHub unreachable: {type(e).__name__}") from None
    if resp.status_code == 204:
        return None
    if resp.status_code != 200:
        raise DispatchError(f"GitHub answered {resp.status_code}")
    try:
        run_id = resp.json().get("workflow_run_id")
    except (ValueError, AttributeError):
        return None
    return run_id if isinstance(run_id, int) and not isinstance(run_id, bool) else None


def github_client():
    with httpx.Client(follow_redirects=False) as client:
        yield client


def dispatch_token() -> str | None:
    return os.environ.get("GITHUB_DISPATCH_TOKEN") or None
