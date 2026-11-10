"""The dev-sql workflow (#101; Plan 02 §9.5): one reviewed SQL file from main, run on dev only, as the dev_sql login.

What is tested is what keeps it from touching anything but dev, or running anything but a reviewed file: the trigger and
inputs, the one secret, the file check (its shell is run here, on a throwaway tree), the login check, the single
transaction, and the rules every file in supabase/dev-sql/ has to follow."""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "dev-sql.yml"
FOLDER = ROOT / "supabase" / "dev-sql"
NAME = re.compile(r"supabase/dev-sql/[0-9]{4}_[a-z0-9_]+\.sql")
FIRST_STATEMENT = re.compile(r"set local role (anon|service_role|job_writer);", re.IGNORECASE)


def _wf():
    return yaml.safe_load(WORKFLOW.read_text())


def _steps():
    return {s.get("name", s.get("uses")): s for s in _wf()["jobs"]["run"]["steps"]}


def test_it_is_manual_has_one_input_and_no_target_to_choose():
    wf = _wf()
    trigger = wf.get("on", wf.get(True))          # YAML 1.1 reads a bare `on` as True
    assert list(trigger) == ["workflow_dispatch"]
    assert list(trigger["workflow_dispatch"]["inputs"]) == ["file"]
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["run"]["environment"] == "dev-sql"
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_the_only_secret_is_dev_sqls_url_and_no_production_or_job_credential_appears():
    text = WORKFLOW.read_text()
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == {"DEV_SQL_URL"}
    for word in ("JOB_DATABASE_URL", "STORAGE_", "ANTHROPIC", "VOYAGE", "SERVICE_ROLE_KEY", "PROD"):
        assert word not in text.replace("service_role", "")        # the role name is fine; its key is not


def test_no_run_line_holds_an_expression_so_inputs_reach_the_scripts_through_env_only():
    for step in _steps().values():
        assert "${{" not in step.get("run", ""), step["name"]       # CLAUDE.md §4
    env = _steps()["Check the file"]["env"]
    assert env["FILE"] == "${{ inputs.file }}"


def test_the_file_runs_in_one_transaction_that_stops_at_the_first_error_with_no_startup_file():
    run = _steps()["Run the file on dev, in one transaction"]["run"]
    assert "-v ON_ERROR_STOP=1" in run and "--single-transaction" in run and '-f "$FILE"' in run and " -X " in run


def _check_step(tmp_path, file, ref="refs/heads/main", content="set local role service_role;\nselect 1;\n", create=True):
    run = _steps()["Check the file"]["run"]
    if create and file.startswith("supabase/dev-sql/") and "\n" not in file:
        target = tmp_path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    done = subprocess.run(["bash", "-e", "-c", run], check=False, cwd=tmp_path,
                          env={"FILE": file, "REF": ref, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    return done.returncode, done.stdout


def test_a_good_file_on_main_passes_the_check(tmp_path):
    assert _check_step(tmp_path, "supabase/dev-sql/0001_smoke.sql")[0] == 0


@pytest.mark.parametrize("file", [
    "../supabase/dev-sql/0001_a.sql", "supabase/dev-sql/../migrations/0001_a.sql", "supabase/dev-sql/sub/0001_a.sql",
    "supabase/dev-sql/0001_A.sql", "supabase/dev-sql/1_a.sql", "supabase/dev-sql/0001_a.sql ", "supabase/dev-sql/0001_a.sql\nx",
    "supabase/dev-sql/0001-a.sql", "supabase/migrations/20261022001100_review_01.sql", "/etc/passwd", "", "0001_a.sql",
])
def test_a_name_outside_the_pattern_is_refused_before_anything_is_read(tmp_path, file):
    code, out = _check_step(tmp_path, file)
    assert code == 1 and "::error title=dev-sql::" in out


def test_a_file_that_is_not_on_main_is_refused(tmp_path):
    code, out = _check_step(tmp_path, "supabase/dev-sql/0002_missing.sql", create=False)
    assert code == 1 and "is not on main" in out


@pytest.mark.parametrize("ref", ["refs/heads/claude/dev-sql-workflow", "refs/pull/1/merge", "refs/tags/v1", ""])
def test_a_run_from_any_ref_but_main_is_refused(tmp_path, ref):
    code, out = _check_step(tmp_path, "supabase/dev-sql/0001_smoke.sql", ref=ref)
    assert code == 1 and "runs only from main" in out


@pytest.mark.parametrize("line", ["\\! env", "  \\copy core.source to stdout", "\\o /tmp/x", "\\i other.sql", "\\gset"])
def test_a_psql_meta_command_is_refused_because_it_would_run_on_the_runner(tmp_path, line):
    code, out = _check_step(tmp_path, "supabase/dev-sql/0003_meta.sql", content=f"set local role anon;\n{line}\n")
    assert code == 1 and "psql meta-command" in out


@pytest.mark.parametrize("line", ["select 1 \\gexec", "select 1; \\! true", "select 'a\\b'", "-- a comment \\! true"])
def test_a_backslash_anywhere_in_the_file_is_refused(tmp_path, line):
    """F1-07: psql runs a meta-command that follows SQL on the same line, so the check is on any backslash."""
    code, out = _check_step(tmp_path, "supabase/dev-sql/0003_meta.sql", content=f"set local role anon;\n{line}\n")
    assert code == 1 and "backslash" in out


def test_the_login_check_names_dev_sql_and_hides_connection_errors():
    run = _steps()["Check the login is dev_sql"]["run"]
    assert "select current_user" in run and '!= "dev_sql"' in run
    assert "2>/dev/null" in run                                     # an error would print the pooler host and project ref


def _files():
    return sorted(p for p in FOLDER.glob("*") if p.is_file())


def test_the_smoke_file_exists():
    assert (FOLDER / "0001_smoke.sql").is_file()


def test_every_file_has_a_valid_name_and_numbers_run_0001_upwards_without_gaps_or_repeats():
    names = [p.relative_to(ROOT).as_posix() for p in _files()]
    assert names and all(NAME.fullmatch(n) for n in names), names
    assert [int(Path(n).name[:4]) for n in names] == list(range(1, len(names) + 1))


def test_every_file_starts_with_a_role_switch_holds_no_meta_command_and_selects_no_star():
    for path in _files():
        lines = [ln for ln in path.read_text().splitlines() if ln.strip() and not ln.strip().startswith("--")]
        assert FIRST_STATEMENT.fullmatch(lines[0].strip()), f"{path.name}: the first statement must be `set local role ...;`"
        assert not any("\\" in ln for ln in lines), f"{path.name}: psql meta-command (no backslash anywhere)"
        assert not re.search(r"select\s+\*", " ".join(lines), re.IGNORECASE), f"{path.name}: select counts, never rows"
