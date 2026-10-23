"""Rosters: config/members.yaml (Spec 01 D12; Prompt 01 B5, B6), loaded into policy.member and policy.member_term.

One entry per person, keyed by slug, under the bank (`fed:`; `rbi:` comes with B6):

    jerome-h-powell:
      name: Jerome H. Powell              # as the statements write it
      aliases: [Jerome Powell]            # other spellings the documents use (the minutes, older statements)
      terms:                              # dated, non-overlapping; `to` is the last day (inclusive), absent = open
        - {role: fed_chair, affiliation: Board of Governors, voter: true, from: 2025-01-28, to: 2026-05-21,
           source: "…"}

Every term needs a source note (Spec 01 D12: one per term); Anand spot-checks. A term is a stretch with one role,
affiliation and FOMC voter status, so a Reserve Bank president's voting year is its own term (the Committee elects
each year's members "for a term beginning" on the January meeting's first day: the minutes' "Annual Organizational
Matters"). An alternate who votes in a member's place keeps `voter: false`: the statement says "X voted as an
alternate member", and the vote row still counts.

Names in a document are matched to people by their name or an alias, compared without case, periods or extra
spaces ("Jerome H. Powell" = "jerome h powell").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "members.yaml"
BANKS = {"fed": "FED", "rbi": "RBI"}
ROLES = {
    "FED": ("fed_chair", "fed_vice_chair", "fed_governor", "fed_bank_president"),
    "RBI": ("rbi_governor", "rbi_deputy_governor", "rbi_executive_director", "mpc_external_member"),
}
SLUG = re.compile(r"^[a-z0-9-]+$")


class RosterError(ValueError):
    """members.yaml doesn't hold a valid roster: nothing is loaded."""


def key(name: str) -> str:
    return " ".join(re.sub(r"[.,]", " ", name).casefold().split())


@dataclass(frozen=True)
class Term:
    role: str
    affiliation: str | None
    is_voter: bool
    start: date
    end: date | None               # the last day, inclusive; None = open
    source: str

    def covers(self, d: date) -> bool:
        return self.start <= d and (self.end is None or d <= self.end)

    @property
    def upper(self) -> date | None:  # the daterange's exclusive upper bound
        return self.end + timedelta(days=1) if self.end else None


@dataclass(frozen=True)
class Member:
    bank: str                      # 'FED' | 'RBI' (policy.member.bank)
    slug: str
    name: str
    aliases: tuple[str, ...]
    terms: tuple[Term, ...]

    def term_on(self, d: date) -> Term | None:
        return next((t for t in self.terms if t.covers(d)), None)


@dataclass(frozen=True)
class Roster:
    members: dict[str, Member]     # by slug

    def match(self, bank: str, name: str) -> Member | None:
        k = key(name)
        found = [m for m in self.members.values() if m.bank == bank and k in {key(m.name), *map(key, m.aliases)}]
        return found[0] if len(found) == 1 else None

    def voters_on(self, bank: str, d: date) -> list[Member]:
        return [m for m in self.members.values() if m.bank == bank and (t := m.term_on(d)) and t.is_voter]


def _date(v, where: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise RosterError(f"members.yaml {where}: not a date: {v!r}") from None


def load(path: Path = CONFIG) -> Roster:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unknown = set(raw) - set(BANKS)
    if unknown:
        raise RosterError(f"members.yaml: unknown banks {sorted(unknown)}")
    members: dict[str, Member] = {}
    names: dict[tuple[str, str], str] = {}
    for bank_key, people in raw.items():
        bank = BANKS[bank_key]
        for slug, v in (people or {}).items():
            where = f"{bank_key}.{slug}"
            if not SLUG.match(str(slug)):
                raise RosterError(f"members.yaml {where}: a slug is lower-case letters, digits and hyphens")
            if slug in members:
                raise RosterError(f"members.yaml {where}: the slug is used twice")
            if not isinstance(v, dict) or not str(v.get("name") or "").strip():
                raise RosterError(f"members.yaml {where}: needs a name")
            extra = set(v) - {"name", "aliases", "terms"}
            if extra:
                raise RosterError(f"members.yaml {where}: unknown keys {sorted(extra)}")
            terms = []
            for i, t in enumerate(v.get("terms") or []):
                tw = f"{where}.terms[{i}]"
                extra = set(t) - {"role", "affiliation", "voter", "from", "to", "source"}
                if extra:
                    raise RosterError(f"members.yaml {tw}: unknown keys {sorted(extra)}")
                if t.get("role") not in ROLES[bank]:
                    raise RosterError(f"members.yaml {tw}: role must be one of {ROLES[bank]}")
                if not str(t.get("source") or "").strip():
                    raise RosterError(f"members.yaml {tw}: every term needs a source note")
                if not isinstance(t.get("voter"), bool):
                    raise RosterError(f"members.yaml {tw}: voter must be true or false")
                start = _date(t.get("from"), tw)
                end = _date(t["to"], tw) if t.get("to") is not None else None
                if end is not None and end < start:
                    raise RosterError(f"members.yaml {tw}: ends before it starts")
                terms.append(Term(t["role"], t.get("affiliation"), t["voter"], start, end, str(t["source"]).strip()))
            if not terms:
                raise RosterError(f"members.yaml {where}: needs at least one term")
            terms.sort(key=lambda t: t.start)
            for a, b in pairwise(terms):
                if a.end is None or a.end >= b.start:
                    raise RosterError(f"members.yaml {where}: terms from {a.start} and {b.start} overlap")
            m = Member(bank, slug, str(v["name"]).strip(), tuple(str(a).strip() for a in v.get("aliases") or ()),
                       tuple(terms))
            for n in {key(m.name), *map(key, m.aliases)}:
                if (bank, n) in names:
                    raise RosterError(f"members.yaml {where}: {n!r} also names {names[(bank, n)]}")
                names[(bank, n)] = slug
            members[slug] = m
    return Roster(members)


def sync(tx, roster: Roster) -> tuple[dict[str, int], int]:
    """Upsert the roster into policy.member and policy.member_term. Returns ({slug: member id}, rows written). A
    member's terms are replaced only when they differ from the file, so a re-run writes nothing."""
    ids, written = {}, 0
    for m in roster.members.values():
        row = tx.execute("select id, name, bank from policy.member where slug = %s", (m.slug,)).fetchone()
        if row is None:
            mid = tx.execute("insert into policy.member (bank, name, slug) values (%s, %s, %s) returning id",
                             (m.bank, m.name, m.slug)).fetchone()[0]
            written += 1
        else:
            mid = row[0]
            if (row[1], row[2]) != (m.name, m.bank):
                tx.execute("update policy.member set name = %s, bank = %s where id = %s", (m.name, m.bank, mid))
                written += 1
        ids[m.slug] = mid
        stored = sorted(
            (r[0], r[1], r[2], r[3], r[4], r[5]) for r in tx.execute(
                """select role, affiliation, is_voter, lower(valid), upper(valid), source_note
                     from policy.member_term where member_id = %s""", (mid,)).fetchall())
        want = sorted((t.role, t.affiliation, t.is_voter, t.start, t.upper, t.source) for t in m.terms)
        if stored != want:
            tx.execute("delete from policy.member_term where member_id = %s", (mid,))
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into policy.member_term (member_id, role, affiliation, is_voter, valid, source_note)
                       values (%s, %s, %s, %s, daterange(%s, %s, '[)'), %s)""",
                    [(mid, t.role, t.affiliation, t.is_voter, t.start, t.upper, t.source) for t in m.terms])
            written += len(m.terms)
    return ids, written
