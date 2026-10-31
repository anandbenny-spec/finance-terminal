"""The headless runner's scripts parse, and its README's command blocks hold no `#` comment lines (#195)."""
import re
import subprocess
from pathlib import Path

import pytest

HEADLESS = Path(__file__).resolve().parents[2] / "ops" / "headless"
SCRIPTS = ("run-job.sh", "run-queue.sh")


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_parses(name):
    path = HEADLESS / name
    assert path.is_file()
    done = subprocess.run(["bash", "-n", str(path)], check=False, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_is_strict_and_executable(name):
    path = HEADLESS / name
    assert "set -euo pipefail" in path.read_text()
    assert path.stat().st_mode & 0o111


def test_readme_command_blocks_have_no_comment_lines():
    text = (HEADLESS / "README.md").read_text()
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.DOTALL)
    assert blocks
    for block in blocks:
        for line in block.splitlines():
            assert not line.lstrip().startswith("#"), line


def test_run_job_keeps_the_run_on_the_api_key():
    text = (HEADLESS / "run-job.sh").read_text()
    assert "claude --bare -p" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "--dangerously-skip-permissions" not in text
    assert "--no-verify" not in text


def test_job_prompt_has_the_issue_placeholder_and_the_stop_rules():
    text = (HEADLESS / "job-prompt.md").read_text()
    assert "{{ISSUE}}" in text
    for label in ("headless", "model:opus", "needs-anand", "needs-mac", "in-progress"):
        assert label in text
