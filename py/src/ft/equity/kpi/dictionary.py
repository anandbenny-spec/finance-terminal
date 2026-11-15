"""The KPI-table pilot's curated per-company dictionary (DD-05, #513; Plan 05 §1.3; Data Audit 10 §8 finding 3). Pure: it
loads `config/equity/kpi_dictionary.yaml` and maps one extracted row (label, member, measure) to one metric key, or to none.

Data Audit 10's automatic dictionary (name tokens overlapping by half) took "Countries with workforce" for workforce and
turned most label drift into false "missing" flags (K6, K7). This one matches only the labels a session curated, each merge
with its reason in the file, after one fixed normalisation:

- a label: lower case, "&" read as "and", every run of non-letters a space, "q o q" read as "qoq" and "y o y" as "yoy",
  and the stray tokens of a period stamp or a possessive ("q", "fy", "h", "s") dropped;
- a member: lower case, "&" read as "and", every run of non-letters a space (no tokens dropped, so a member "FY25" or "Q4"
  never reads as the empty member, the company total), then the metric's aliases;
- the member's words are taken out of the label once (on both sides), so "Geography Composition (%): UK" with member "UK"
  and "geography composition" are one label.

A metric with `members` is one key per member (`<key>.<member>`; the empty member is the total, `<key>.total`); a metric
with `member` is one key that accepts those members (default: only the empty one). Two metrics of a company never share a
(label, member, measure): `load` refuses such a file, so a row maps to at most one key. An unmapped row never makes or
answers a flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[4] / "config" / "equity" / "kpi_dictionary.yaml"
VERSION = 1
_DROP = frozenset({"q", "fy", "h", "s"})
_QOQ = re.compile(r"\bq ?o ?q\b")
_YOY = re.compile(r"\by ?o ?y")


def _letters(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z]+", " ", s).split())


def norm_label(s: str | None) -> str:
    """A label as the dictionary compares it (the module doc's first bullet)."""
    s = _letters(s)
    s = _QOQ.sub(" qoq ", s)
    s = _YOY.sub(" yoy ", s)
    return " ".join(t for t in s.split() if t not in _DROP)


def norm_member(s: str | None) -> str:
    """A member as the dictionary compares it: letters only, no tokens dropped ("FY25" reads "fy", never "")."""
    return _letters(s)


def core(label: str | None, member: str | None) -> str:
    """The label with the member's words taken out once (both normalised)."""
    lab, mem = norm_label(label), norm_label(member)
    if mem:
        padded = f" {lab} "
        i = padded.find(f" {mem} ")
        if i >= 0:
            lab = " ".join((padded[:i] + " " + padded[i + len(mem) + 2:]).split())
    return lab


def slug(member: str) -> str:
    return member.replace(" ", "_") if member else "total"


@dataclass(frozen=True)
class Metric:
    """One flaggable metric: a dictionary entry, or one member of a breakdown entry."""
    key: str                      # `workforce`, `industry_share.bfsi`
    label: str                    # the display name kept on the flag (`metric_label`)
    unit: str                     # the curated unit kind (rule 4)
    terms: tuple[str, ...]        # words a speaker uses for it (the transcript candidates)
    member_terms: tuple[str, ...] = ()   # for a breakdown member: its name and aliases (a sentence must name one)
    measures: tuple[str, ...] = ()       # the curated measure kinds (rule 4 compares them with the unit, #513 part 2)

    @property
    def kind(self) -> tuple[str, tuple[str, ...]]:
        """What rule 4's "matching units" compares: the curated unit and the measure kinds. A share and a growth rate
        both print "%", but a level in % and a change in % aren't the same unit (#513 part 2, from dev run 13's TCS
        attrition, which rule 4 paired with a new QoQ growth rate)."""
        return (self.unit, self.measures)


@dataclass
class Company:
    slug: str
    document_class: str
    quarters: tuple[str, ...]
    metrics: dict[str, Metric] = field(default_factory=dict)
    index: dict[tuple[str, str, str], str] = field(default_factory=dict)   # (core label, member, measure) → key

    def key_of(self, label: str | None, member: str | None, measure: str) -> str | None:
        """The metric key one extracted row maps to, or None."""
        m = norm_member(member)
        return self.index.get((core(label, member), m, measure))


class DictionaryError(ValueError):
    pass


def _company(slug_: str, raw: dict) -> Company:
    co = Company(slug_, raw["document_class"], tuple(raw["quarters"]))
    for entry in raw["metrics"]:
        key = entry["key"]
        measures = entry["measure"] if isinstance(entry["measure"], list) else [entry["measure"]]
        labels = [x["label"] for x in entry["labels"]]
        terms = tuple(t.lower() for t in entry.get("terms") or ())
        kinds = tuple(sorted(set(measures)))
        for x in entry["labels"][1:]:
            if not (x.get("why") or "").strip():
                raise DictionaryError(f"{slug_}.{key}: the merged label {x['label']!r} has no reason")
        for x in entry["labels"]:
            if len(x.get("seen", "")) != len(co.quarters):
                raise DictionaryError(f"{slug_}.{key}: {x['label']!r} has no `seen` for each quarter")
        if "members" in entry:
            members = {norm_member(m): d for m, d in entry["members"].items()}
            aliases = {norm_member(a): norm_member(c) for a, c in (entry.get("aliases") or {}).items()}
            for a, c in aliases.items():
                if c not in members:
                    raise DictionaryError(f"{slug_}.{key}: the alias {a!r} names no member")
            for canon, display in members.items():
                k = f"{key}.{slug(canon)}"
                said = (canon, *[a for a, c in aliases.items() if c == canon]) if canon else ()
                metric = Metric(k, f"{entry['name']}: {display}", str(entry["unit"]), terms, tuple(x for x in said if x),
                                kinds)
                _add(co, metric, labels, measures, [canon, *[a for a, c in aliases.items() if c == canon]])
        else:
            accepted = [norm_member(m) for m in (entry.get("member") or [""])]
            _add(co, Metric(key, entry["name"], str(entry["unit"]), terms, (), kinds), labels, measures, accepted)
    return co


def _add(co: Company, metric: Metric, labels: list[str], measures: list[str], members: list[str]) -> None:
    if metric.key in co.metrics:
        raise DictionaryError(f"{co.slug}: the key {metric.key!r} is defined twice")
    co.metrics[metric.key] = metric
    for raw_member in members:
        for lab in labels:
            c = core(lab, raw_member)
            if not c and not raw_member:
                raise DictionaryError(f"{co.slug}.{metric.key}: the label {lab!r} is empty once normalised")
            for measure in measures:
                k = (c, raw_member, measure)
                if co.index.get(k, metric.key) != metric.key:
                    raise DictionaryError(f"{co.slug}: {k} maps to both {co.index[k]!r} and {metric.key!r}")
                co.index[k] = metric.key


def load(path: Path = PATH) -> dict[str, Company]:
    """The dictionary by company slug; refuses a file whose version isn't this module's, a merge without its reason, or
    two metrics that share a (label, member, measure)."""
    raw = yaml.safe_load(path.read_text())
    if raw.get("version") != VERSION:
        raise DictionaryError(f"dictionary version {raw.get('version')!r}, expected {VERSION}")
    return {s: _company(s, c) for s, c in (raw.get("companies") or {}).items()}
