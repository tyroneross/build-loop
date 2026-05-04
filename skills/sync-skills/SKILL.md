---
name: build-loop:sync-skills
description: Drift-detection for build-loop's native skills copied from canonical upstream repos (NavGator, claude-code-debugger). Walks skills/architecture/ and skills/debugging/, recomputes source_hash, reports drift. Read-only — never auto-updates.
version: 0.1.0
user-invocable: true
---

# Sync-Skills — Drift Detection for Native Copies

Build-loop's `skills/architecture/` and `skills/debugging/` skills are copied from canonical upstream repos (NavGator, claude-code-debugger). Each carries a `source:` path and `source_hash:` SHA-256 in its frontmatter. This skill recomputes the hash from the canonical source file and reports any drift.

**Read-only.** Never auto-updates a skill — surfaces a list of skills that need refresh and a one-line refresh command.

## When to Activate

- User asks "check skill drift", "sync skills", "are native skills stale"
- After updating NavGator or claude-code-debugger locally and you want to know which build-loop skills inherit the change
- Periodic maintenance (run quarterly, or before any release that touches the architecture/debugging surfaces)

## Workflow

Run the drift-check script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_skills.py
```

Or inline (no script dependency):

```bash
python3 - <<'PY'
import hashlib, os, pathlib, re, sys

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[2])
HOME = pathlib.Path.home()

def read_frontmatter(path):
    text = path.read_text()
    m = re.match(r'^---\n(.*?)\n---\n', text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm

def find_canonical(rel_path):
    # Try common parent dirs
    for parent in [HOME / "dev" / "git-folder", ROOT.parent, pathlib.Path.cwd()]:
        candidate = parent / rel_path
        if candidate.exists():
            return candidate
    return None

drift = []
checked = 0
for tree in ["skills/architecture", "skills/debugging"]:
    for skill_md in (ROOT / tree).rglob("SKILL.md"):
        fm = read_frontmatter(skill_md)
        src = fm.get("source")
        expected_hash = fm.get("source_hash")
        if not src or not expected_hash:
            continue
        canonical = find_canonical(src)
        rel = skill_md.relative_to(ROOT)
        checked += 1
        if not canonical:
            drift.append((str(rel), "MISSING", "canonical source not found: " + src))
            continue
        actual = hashlib.sha256(canonical.read_bytes()).hexdigest()
        if actual != expected_hash:
            drift.append((str(rel), "DRIFT", f"expected {expected_hash[:12]} got {actual[:12]}"))

print(f"Checked: {checked} skills")
if not drift:
    print("Status: clean — no drift")
    sys.exit(0)

print(f"Status: {len(drift)} drift(s)")
for rel, kind, detail in drift:
    print(f"  [{kind}] {rel}")
    print(f"          {detail}")
print()
print("Refresh: rerun build-loop:sync-skills with --refresh after reviewing the canonical diff,")
print("or invoke the orchestrator to re-import the affected skill(s).")
sys.exit(1 if drift else 0)
PY
```

## Output

- **Clean**: `Checked: 10 skills. Status: clean — no drift.`
- **Drift detected**: list of `<skill path> | DRIFT | expected <hash-prefix> got <hash-prefix>` plus one-line refresh hint
- **Missing canonical**: list of `<skill path> | MISSING | canonical source not found: <path>` — means upstream repo moved or was deleted

## What This Skill Does NOT Do

- Does not auto-update SKILL.md files (refresh is a deliberate, reviewed action)
- Does not modify the canonical source repos
- Does not re-import — the user (or build-orchestrator on user request) re-runs the import flow

## How to Refresh a Drifted Skill

1. Read the canonical source file at the path in `source:`
2. Diff against the local SKILL.md (skipping the rewritten frontmatter and any build-loop-specific sibling-skill references)
3. Decide: pull the upstream change, keep local fork, or escalate to user
4. If pulling: re-author the local SKILL.md preserving the build-loop-specific framing (skill name, sibling references), recompute `source_hash`, update frontmatter
5. Re-run `build-loop:sync-skills` to confirm drift cleared

## Locations Walked

- `skills/architecture/scan/SKILL.md`
- `skills/architecture/impact/SKILL.md`
- `skills/architecture/trace/SKILL.md`
- `skills/architecture/rules/SKILL.md`
- `skills/architecture/dead/SKILL.md`
- `skills/architecture/review/SKILL.md`
- `skills/debugging/memory/SKILL.md`
- `skills/debugging/store/SKILL.md`
- `skills/debugging/assess/SKILL.md`
- `skills/debugging/debug-loop/SKILL.md`

## Sibling Skills

- All skills under `build-loop:architecture-*` — drift-checked by this skill
- All skills under `build-loop:debugging-*` — drift-checked by this skill

*Native to build-loop. Not copied from any upstream — this is the drift-detector itself.*
