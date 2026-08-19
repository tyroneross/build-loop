# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Adequacy of the REAL routing table — not the generator's mechanics.

Why this file exists separately from ``test_skill_index.py``: that suite proves
``truncate()`` is deterministic and bounded, and runs against a synthetic
two-skill ``tmp_path`` fixture. Both are true and neither could ever catch the
defect found 2026-08-18 — a 160-char head-truncation deleted every ``NOT for``
disambiguator, because this repo's house style puts it last. A deterministic bug
regenerates byte-identically forever, so ``--check`` passed clean the whole time.

The escape path was that NO test read ``docs/SKILL-INDEX.md`` itself. These do.
They assert what an agent actually sees when choosing a skill.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "SKILL-INDEX.md"
SKILLS = sorted(ROOT.glob("skills/**/SKILL.md"))


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        km = re.match(r"^([a-zA-Z_-]+):\s*(.*)$", line)
        if km:
            key = km.group(1)
            out[key] = km.group(2).strip().strip("\"'")
        elif key and line.startswith(" "):
            out[key] += " " + line.strip()
    return out


def _index_text() -> str:
    return INDEX.read_text(encoding="utf-8", errors="replace")


# --- 1. Disambiguators must survive into the rendered table ----------------
DISAMBIGUATOR = re.compile(r"(NOT for[^.]*\.|Distinct from[^.]*\.)", re.I)


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_disambiguator_survives_into_the_rendered_row(skill: Path) -> None:
    """A `NOT for X (use Y)` clause is the sentence that decides between two
    similar skills. If the table drops it, the router never sees it."""
    desc = _frontmatter(skill).get("description", "")
    clauses = DISAMBIGUATOR.findall(desc)
    if not clauses:
        pytest.skip("no disambiguator to preserve")
    rendered = " ".join(_index_text().split())
    for clause in clauses:
        needle = " ".join(clause.split())[:60]
        assert needle in rendered, (
            f"{skill.relative_to(ROOT)}: disambiguator missing from "
            f"docs/SKILL-INDEX.md — an agent routing from the table cannot "
            f"tell this skill apart from the one it excludes.\n  lost: {needle!r}"
        )


# --- 2. Every routable skill must say WHEN, not only WHAT -----------------
TRIGGER_MARKERS = (
    "use when", "use this", "use before", "use after", "invoke", "trigger",
    "fires", "activates", "run before", "run after", "call when",
    "when the user", "when a", "when an", "asks to", "asks for", "reach for",
)

#: Known gaps, each owed a trigger sentence. SHRINK this; never grow it.
#: Emptied 2026-08-18 — drain-proposals, ibr-bridge and recursive-retrospective
#: all gained trigger clauses. The inverted assertion below is what forced the
#: shrink: a skill listed here that now HAS a trigger fails, so a fixed gap
#: cannot sit in the allowlist pretending to still be broken.
NO_TRIGGER_ALLOWLIST: set[str] = set()


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_description_states_when_to_use_it(skill: Path) -> None:
    fm = _frontmatter(skill)
    blob = (fm.get("description", "") + " " + fm.get("when_to_use", "")).lower()
    has = any(m in blob for m in TRIGGER_MARKERS)
    if skill.parent.name in NO_TRIGGER_ALLOWLIST:
        assert not has, (
            f"{skill.parent.name} now states WHEN to use it — remove it from "
            f"NO_TRIGGER_ALLOWLIST so the gap cannot silently return"
        )
        pytest.skip("known gap, tracked in NO_TRIGGER_ALLOWLIST")
    assert has, (
        f"{skill.relative_to(ROOT)}: description says what the skill IS but "
        f"never WHEN to reach for it. An agent cannot route on this."
    )


# --- 3. Two skills must not claim the same trigger without a boundary -----
#: Clusters where two or more skills genuinely compete for the same request,
#: confirmed by three independent audits (2026-08-18). A CURATED list, not a
#: phrase scan: scanning flagged `research` against `plugin-tests` and
#: `ui-design` because they mention the word in passing. A gate that cries wolf
#: gets switched off, so this only contains pairs a router would actually
#: confuse. Add a cluster when a real collision is found; never auto-generate.
COLLISION_CLUSTERS = (
    ("debug-loop", "debugging-memory", "root-cause-analysis"),
    ("self-improve", "recursive-retrospective"),
    ("architecture-rules", "architecture-dead"),
)


@pytest.mark.parametrize("cluster", COLLISION_CLUSTERS, ids=lambda c: "+".join(c))
def test_competing_skills_name_each_other(cluster: tuple[str, ...]) -> None:
    """Each skill in a competing cluster must name at least one sibling.

    Without it the router has no basis to choose and falls back to listing
    order, which is arbitrary. One sentence per skill closes it.
    """
    by_name = {}
    for skill in SKILLS:
        fm = _frontmatter(skill)
        key = skill.parent.name
        if skill.parent.parent.name == "architecture":
            key = f"architecture-{key}"
        by_name[key] = (fm.get("description", "") + " " + fm.get("when_to_use", "")).lower()

    missing = []
    for name in cluster:
        desc = by_name.get(name)
        if desc is None:
            pytest.skip(f"{name} not found on disk")
        siblings = [s for s in cluster if s != name]
        bare = [s.replace("architecture-", "") for s in siblings]
        if not any(s in desc or b in desc for s, b in zip(siblings, bare)):
            missing.append(name)
    assert not missing, (
        f"These skills compete for the same request but name no sibling: "
        f"{missing}. Add one sentence to each description saying which sibling "
        f"owns the neighbouring case, e.g. 'NOT for X (use Y)'. Cluster: {list(cluster)}"
    )


#: The disambiguator must appear EARLY, not merely exist. Even a 400-char cap
#: loses 3 of 11 today, because the skills needing the most disambiguation wrote
#: the longest descriptions — root-cause-analysis's clause starts at char 729.
#: Rendering is uncapped now, so nothing is lost; this enforces the ORDERING
#: convention so a future 900-char description cannot bury its boundary again.
DISAMBIGUATOR_MUST_START_BY = 300

#: Descriptions whose boundary clause is currently buried. Each is owed a
#: reorder to `[trigger]. NOT for X (use Y). [elaboration].`
#: SHRINK this list. Never grow it.
#: Shrunk 2026-08-18 from six to two: model-tiering (217), debugging-memory
#: (232), cost-rca (238) and model-bakeoff (240) were front-loaded and now clear
#: the threshold. root-cause-analysis (312) and color-engine (364) still bury
#: theirs — both need their opening trigger sentence tightened, not just
#: resequenced, because the trigger clause itself is wordy.
BURIED_DISAMBIGUATOR_ALLOWLIST = {"color-engine", "root-cause-analysis"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_disambiguator_appears_early_enough_to_be_read(skill: Path) -> None:
    desc = _frontmatter(skill).get("description", "")
    m = DISAMBIGUATOR.search(desc)
    if not m:
        pytest.skip("no disambiguator")
    early = m.start() <= DISAMBIGUATOR_MUST_START_BY
    if skill.parent.name in BURIED_DISAMBIGUATOR_ALLOWLIST:
        assert not early, (
            f"{skill.parent.name}'s disambiguator now starts at char {m.start()} — "
            f"remove it from BURIED_DISAMBIGUATOR_ALLOWLIST so it cannot regress"
        )
        pytest.skip("known buried disambiguator, tracked in allowlist")
    assert early, (
        f"{skill.relative_to(ROOT)}: disambiguator starts at char {m.start()}, "
        f"past {DISAMBIGUATOR_MUST_START_BY}. Reorder to "
        f"'[trigger]. NOT for X (use Y). [elaboration].' — a reader skimming "
        f"the routing table stops long before this."
    )
