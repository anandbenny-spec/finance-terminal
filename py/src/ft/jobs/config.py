"""config/tasks.yaml, the source of truth for what runs when (Architecture 02 §1.3).

Each run loads it, checks it and upserts it into ops.task (the health page's mirror) before anything runs.
Due-rule names are kept as tasks.yaml writes them (fomc_decision_day, mpc_decision_day, after_decision_document;
Prompt 01 A2); due.py evaluates them. A daily rule may add a catch-up after its interval: `every 20 hours or
fomc_catch_up` is due when 20 hours have passed, or while the FOMC decision document of a recent meeting isn't
stored (CATCH_UPS names the three; Decision days, issue #158).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "tasks.yaml"

WORKFLOWS = ("daily", "watch", "weekly")          # the workflows a task can belong to (ops.task.workflows check)
NAMED_RULES = ("fomc_decision_day", "mpc_decision_day", "after_decision_document")
CATCH_UPS = {                                     # `<every rule> or <name>`: the named rule's catch-up, in any workflow
    "fomc_catch_up": "fomc_decision_day",
    "mpc_catch_up": "mpc_decision_day",
    "ai_catch_up": "after_decision_document",
}
KEYS = {"description", "workflows", "run_after", "overdue_after", "uses_nse", "visitor_refreshable", "enabled"}
_KEY = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")    # ops.task.key check
_INTERVAL = re.compile(r"^(\d+) (minute|hour|day)s?$")


class ConfigError(ValueError):
    pass


def parse_interval(text: str) -> timedelta:
    m = _INTERVAL.match(str(text).strip())
    if not m or int(m[1]) <= 0:
        raise ConfigError(f"not an interval: {text!r} (write e.g. '20 hours' or '25 days')")
    return timedelta(**{m[2] + "s": int(m[1])})


def split_rule(rule: str) -> tuple[str, str | None]:
    """('every 20 hours', 'fomc_catch_up') for 'every 20 hours or fomc_catch_up'; (rule, None) without a catch-up."""
    base, sep, extra = rule.partition(" or ")
    return (base.strip(), extra.strip()) if sep else (rule, None)


def every_interval(rule: str) -> timedelta | None:
    """The interval of an `every <interval>` rule (with or without a catch-up), or None for a named rule."""
    base, _ = split_rule(rule)
    return parse_interval(base[len("every "):]) if base.startswith("every ") else None


@dataclass(frozen=True)
class TaskConfig:
    key: str
    description: str
    rules: dict[str, str]                 # workflow -> due rule
    run_after: tuple[str, ...]
    overdue_after: timedelta
    uses_nse: bool = False
    visitor_refreshable: bool = False
    enabled: bool = True

    @property
    def module(self) -> str:
        return self.key.split(".")[0]

    @property
    def workflows(self) -> tuple[str, ...]:
        return tuple(self.rules)

    @property
    def min_interval(self) -> timedelta:
        """The shortest `every` rule: not run again sooner (ops.task.min_interval; NSE's minimum on refreshes)."""
        return min(i for i in map(every_interval, self.rules.values()) if i is not None)


def _task(key: str, raw: object) -> TaskConfig:
    if not _KEY.match(key):
        raise ConfigError(f"{key}: a task key is <module>.<name> in lower case")
    if not isinstance(raw, dict):
        raise ConfigError(f"{key}: expected a mapping")
    if unknown := set(raw) - KEYS:
        raise ConfigError(f"{key}: unknown keys {sorted(unknown)}")
    rules = raw.get("workflows")
    if not isinstance(rules, dict) or not rules:
        raise ConfigError(f"{key}: workflows must map each workflow to its due rule")
    for wf, rule in rules.items():
        if wf not in WORKFLOWS:
            raise ConfigError(f"{key}: unknown workflow {wf!r} (one of {', '.join(WORKFLOWS)})")
        if not isinstance(rule, str) or not (rule in NAMED_RULES or rule.startswith("every ")):
            raise ConfigError(f"{key}: unknown due rule {rule!r} in {wf} "
                              f"(every <interval>, or one of {', '.join(NAMED_RULES)})")
        _, catch_up = split_rule(rule)
        if catch_up is not None and catch_up not in CATCH_UPS:
            raise ConfigError(f"{key}: unknown catch-up {catch_up!r} in {wf} "
                              f"(`every <interval> or` one of {', '.join(CATCH_UPS)})")
        every_interval(rule)
    if not any(every_interval(r) for r in rules.values()):
        raise ConfigError(f"{key}: needs at least one `every <interval>` rule (its minimum interval)")
    flags = {}
    for flag, default in (("uses_nse", False), ("visitor_refreshable", False), ("enabled", True)):
        flags[flag] = raw.get(flag, default)
        if not isinstance(flags[flag], bool):
            raise ConfigError(f"{key}: {flag} must be true or false")
    if flags["uses_nse"] and flags["visitor_refreshable"]:
        raise ConfigError(f"{key}: an NSE task is never visitor-refreshable (brief §6.2)")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ConfigError(f"{key}: needs a description")
    run_after = raw.get("run_after", [])
    if not isinstance(run_after, list) or not all(isinstance(k, str) for k in run_after):
        raise ConfigError(f"{key}: run_after must be a list of task keys")
    return TaskConfig(key=key, description=description.strip(), rules=dict(rules), run_after=tuple(run_after),
                      overdue_after=parse_interval(raw.get("overdue_after", "")), **flags)


def load(path: str | Path = CONFIG) -> dict[str, TaskConfig]:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{path}: expected a mapping of task keys")
    tasks = {key: _task(key, value) for key, value in raw.items()}
    for t in tasks.values():
        if missing := [k for k in t.run_after if k not in tasks]:
            raise ConfigError(f"{t.key}: run_after names unknown tasks {missing}")
    return tasks


def sync(conn, tasks: dict[str, TaskConfig]) -> None:
    """Upsert the config into ops.task. Rows for keys the config doesn't name are left as they are."""
    with conn.transaction():
        for t in tasks.values():
            conn.execute(
                """insert into ops.task (key, module, description, workflows, due_rules, run_after, min_interval,
                                         overdue_after, uses_nse, visitor_refreshable, enabled, synced_at)
                   values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, now())
                   on conflict (key) do update set
                     module = excluded.module, description = excluded.description, workflows = excluded.workflows,
                     due_rules = excluded.due_rules, run_after = excluded.run_after,
                     min_interval = excluded.min_interval, overdue_after = excluded.overdue_after,
                     uses_nse = excluded.uses_nse, visitor_refreshable = excluded.visitor_refreshable,
                     enabled = excluded.enabled, synced_at = excluded.synced_at""",
                (t.key, t.module, t.description, list(t.workflows), json.dumps(t.rules), list(t.run_after),
                 t.min_interval, t.overdue_after, t.uses_nse, t.visitor_refreshable, t.enabled))
