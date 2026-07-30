# Extensions + Maintainer Mode — Implementation Plan (roadmap)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every build-loop user a personal capability layer (`~/.build-loop-extensions/`) that survives core updates — learned skills/agents land in a `pending/` draft zone, load only after explicit approval, and are version-controlled separately from core.

**Architecture:** Core + userland overlay. The overlay's `plugin/` dir registers as a Claude Code skills-directory plugin (loads in place, namespaced, update-proof); `pending/` is a *sibling outside the plugin root* so it structurally cannot load until `approve` does a `git mv` into `plugin/`. Promotion routing is identity-driven (P2). Source of truth: `docs/design/extensions-and-maintainer-mode.md`.

**Tech Stack:** Python 3 (stdlib only — matches build-loop's script discipline), Claude Code skills-dir plugin mechanism, git.

**Scope:** This plan fully details **P1** (the MVP: learning survives updates). **P2–P5 are outlined** at the end — each becomes its own plan once P1's load-bearing spike (Task 1) confirms the loading model.

---

## File Structure (P1)

| File | Responsibility |
|---|---|
| `scripts/privacy.py` (new) | Reusable secret/PII deny-pattern scanner, extracted from `install_memory.validate_public_seed`. |
| `scripts/extensions_paths.py` (new) | Resolve the extensions root (`$BUILD_LOOP_EXTENSIONS_ROOT` → `~/.build-loop-extensions`) + sub-paths. One place owns the layout. |
| `scripts/extensions_init.py` (new) | Scaffold the extensions dir: `plugin/` + `pending/` + versioned manifest + git init + `graduated.json`. Idempotent. |
| `scripts/extensions_check.py` (new) | Deterministic pre-approval checks: frontmatter schema, `ext-` namespace, privacy deny-scan, trigger-overlap vs core/active. |
| `scripts/extensions_approve.py` (new) | `--list` pending; approve one (`git mv pending → plugin`) after checks pass. |
| `scripts/extensions_route.py` (new) | Consumer-default router: place a drafted artifact into `pending/`. Called by Phase 6 Learn. |
| `scripts/extensions_pending_count.py` (new) | Count pending drafts (for the session-start/run-end nudge). |
| `skills/build-loop/references/phase-6-learn.md` (modify) | Document that drafts route to `extensions/pending/` via `extensions_route.py`. |
| `hooks/session-start-extensions.sh` (new) + `hooks/hooks.json` (modify) | Surface "N pending extension drafts" at session start. |
| `tests/test_extensions_*.py` (new) | One test module per script above. |

---

## Task 1: Load-bearing spike — does `pending/` stay unloaded?

**Fable flagged this as a 30-minute empirical check that MUST pass before building P1.** If a skills-dir plugin rooted at `plugin/` loads skills from a sibling `pending/`, the entire safety gate is fiction and the layout must change.

**Files:** none committed — throwaway under `/tmp`.

- [ ] **Step 1: Build a throwaway overlay**

```bash
T=$(mktemp -d); mkdir -p "$T/ext/plugin/.claude-plugin" "$T/ext/plugin/skills/ext-spike-active" "$T/ext/pending/skills/ext-spike-pending"
cat > "$T/ext/plugin/.claude-plugin/plugin.json" <<'JSON'
{ "name": "build-loop-extensions", "version": "0.1.0", "description": "spike" }
JSON
printf -- '---\nname: ext-spike-active\ndescription: ACTIVE spike skill, should load\n---\nactive\n' > "$T/ext/plugin/skills/ext-spike-active/SKILL.md"
printf -- '---\nname: ext-spike-pending\ndescription: PENDING spike skill, must NOT load\n---\npending\n' > "$T/ext/pending/skills/ext-spike-pending/SKILL.md"
echo "$T"
```

- [ ] **Step 2: Register `plugin/` as a skills-dir plugin and start a fresh session**

Run (in a throwaway HOME or via the documented skills-dir mechanism confirmed in the platform check):
```bash
ln -s "$T/ext/plugin" ~/.claude/skills/build-loop-extensions-spike
```
Start a new Claude Code session.

- [ ] **Step 3: Verify load behavior**

In the new session, list available skills.
Expected PASS: `build-loop-extensions-spike:ext-spike-active` is present; `ext-spike-pending` is **absent**.

- [ ] **Step 4: Record the verdict**

If PASS → proceed to Task 2 unchanged.
If FAIL (pending loaded) → STOP. The layout is wrong; pending must move further out (e.g. a separate non-registered dir) — revise the design's layout section before continuing. Do not build on a failed gate.

- [ ] **Step 5: Clean up**

```bash
rm -f ~/.claude/skills/build-loop-extensions-spike; rm -rf "$T"
```

---

## Task 2: `scripts/privacy.py` — reusable deny-pattern scanner

**Files:**
- Create: `scripts/privacy.py`
- Test: `tests/test_privacy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_privacy.py
import sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from privacy import scan_text, load_default_patterns  # noqa: E402

class PrivacyScanTests(unittest.TestCase):
    def test_flags_known_pii_and_secrets(self):
        pats = ["(?i)tyroneross", r"/Users/[^/\s`]+", r"gh[pousr]_[A-Za-z0-9_]{20,}"]
        hits = scan_text("path /Users/alice and ghp_ABCDEFGHIJKLMNOPQRSTUV", pats)
        self.assertTrue(any("/Users/alice" in h["match"] for h in hits))
        self.assertTrue(any(h["match"].startswith("ghp_") for h in hits))

    def test_clean_text_no_hits(self):
        self.assertEqual(scan_text("a generic skill that lints YAML", ["(?i)tyroneross"]), [])

    def test_loads_patterns_from_memory_manifest(self):
        pats = load_default_patterns()
        self.assertIn("(?i)tyroneross", pats)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test_privacy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'privacy'`.

- [ ] **Step 3: Implement `scripts/privacy.py`**

```python
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""privacy.py — reusable secret/PII deny-pattern scanner.

The canonical pattern list lives in templates/memory/manifest.json privacy.deny_patterns
(the memory public-seed allowlist). Extension checks reuse it so the two surfaces
never drift. Pure stdlib.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_MANIFEST = HERE.parent / "templates" / "memory" / "manifest.json"


def load_default_patterns(manifest_path: Path | None = None) -> list[str]:
    path = manifest_path or _MANIFEST
    data = json.loads(path.read_text())
    pats = data.get("privacy", {}).get("deny_patterns", [])
    return [p for p in pats if isinstance(p, str)]


def scan_text(text: str, patterns: list[str]) -> list[dict[str, Any]]:
    """Return [{pattern, match}] for every deny-pattern hit. Invalid regexes are skipped."""
    hits: list[dict[str, Any]] = []
    for pat in patterns:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for m in rx.finditer(text):
            hits.append({"pattern": pat, "match": m.group(0)})
    return hits


def scan_file(path: Path, patterns: list[str] | None = None) -> list[dict[str, Any]]:
    pats = patterns if patterns is not None else load_default_patterns()
    return scan_text(path.read_text(errors="ignore"), pats)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 tests/test_privacy.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/privacy.py tests/test_privacy.py
git commit scripts/privacy.py tests/test_privacy.py -m "feat(extensions): reusable privacy deny-scan extracted from memory seed"
```

---

## Task 3: `scripts/extensions_paths.py` — layout resolver

**Files:**
- Create: `scripts/extensions_paths.py`
- Test: `tests/test_extensions_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extensions_paths.py
import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_paths import root, plugin_dir, pending_dir, manifest_path  # noqa: E402

class PathsTests(unittest.TestCase):
    def test_env_override(self):
        os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = "/tmp/blx-test"
        try:
            self.assertEqual(root(), Path("/tmp/blx-test"))
            self.assertEqual(plugin_dir(), Path("/tmp/blx-test/plugin"))
            self.assertEqual(pending_dir(), Path("/tmp/blx-test/pending"))
            self.assertEqual(manifest_path(), Path("/tmp/blx-test/plugin/.claude-plugin/plugin.json"))
        finally:
            del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]

    def test_default_is_hyphenated_home(self):
        os.environ.pop("BUILD_LOOP_EXTENSIONS_ROOT", None)
        self.assertEqual(root(), Path.home() / ".build-loop-extensions")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test_extensions_paths.py -v` — Expected: FAIL (no module).

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_paths.py — single owner of the ~/.build-loop-extensions layout."""
from __future__ import annotations
import os
from pathlib import Path

ENV = "BUILD_LOOP_EXTENSIONS_ROOT"

def root() -> Path:
    return Path(os.environ[ENV]) if os.environ.get(ENV) else Path.home() / ".build-loop-extensions"

def plugin_dir() -> Path: return root() / "plugin"
def pending_dir() -> Path: return root() / "pending"
def manifest_path() -> Path: return plugin_dir() / ".claude-plugin" / "plugin.json"
def graduated_path() -> Path: return root() / "graduated.json"
```

- [ ] **Step 4: Run to verify it passes** — `python3 tests/test_extensions_paths.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/extensions_paths.py tests/test_extensions_paths.py
git commit scripts/extensions_paths.py tests/test_extensions_paths.py -m "feat(extensions): layout resolver"
```

---

## Task 4: `scripts/extensions_init.py` — scaffold (idempotent)

**Files:**
- Create: `scripts/extensions_init.py`
- Test: `tests/test_extensions_init.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extensions_init.py
import os, sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_init import ensure_scaffold  # noqa: E402

class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.tmp.name
    def tearDown(self):
        del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]; self.tmp.cleanup()

    def test_creates_structure_and_versioned_manifest(self):
        ensure_scaffold(git_init=False)
        r = Path(self.tmp.name)
        self.assertTrue((r / "plugin" / "skills").is_dir())
        self.assertTrue((r / "pending").is_dir())
        m = json.loads((r / "plugin" / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(m["name"], "build-loop-extensions")
        self.assertTrue(m.get("version"))  # required for codex sync path
        self.assertEqual(json.loads((r / "graduated.json").read_text()), {"absorbed": []})

    def test_idempotent(self):
        ensure_scaffold(git_init=False); ensure_scaffold(git_init=False)  # no raise

    def test_registers_only_plugin_root(self):
        import extensions_init
        home = tempfile.TemporaryDirectory(); os.environ["HOME"] = home.name
        try:
            ensure_scaffold(git_init=False)
            res = extensions_init.register_skills_dir()
            self.assertTrue(res["registered"])
            link = Path(home.name) / ".claude" / "skills" / "build-loop-extensions"
            self.assertTrue(link.is_symlink())
            # the link points at plugin/ ONLY — pending/ is never reachable through it
            self.assertEqual(link.resolve(), (Path(self.tmp.name) / "plugin").resolve())
            self.assertEqual(extensions_init.register_skills_dir().get("noop"), True)  # idempotent
        finally:
            home.cleanup()
```
> Note: `register_skills_dir` reads `Path.home()`, which honors `$HOME`; the test overrides it. Restore the real `HOME` in `tearDown` if other tests depend on it.

- [ ] **Step 2: Run to verify it fails** — `python3 tests/test_extensions_init.py -v` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_init.py — scaffold ~/.build-loop-extensions (idempotent)."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extensions_paths import root, plugin_dir, pending_dir, manifest_path, graduated_path  # noqa: E402

MANIFEST = {"name": "build-loop-extensions", "version": "0.1.0",
            "description": "Per-user learned build-loop skills/agents. Loads only approved (active) artifacts."}

def ensure_scaffold(git_init: bool = True) -> dict:
    for d in (plugin_dir() / "skills", plugin_dir() / "agents", plugin_dir() / "config", pending_dir() / "skills"):
        d.mkdir(parents=True, exist_ok=True)
    mp = manifest_path(); mp.parent.mkdir(parents=True, exist_ok=True)
    if not mp.exists():
        mp.write_text(json.dumps(MANIFEST, indent=2) + "\n")
    gp = graduated_path()
    if not gp.exists():
        gp.write_text(json.dumps({"absorbed": []}, indent=2) + "\n")
    if git_init and not (root() / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root())], check=False)
    return {"root": str(root()), "ok": True}

def register_skills_dir() -> dict:
    """Durably register plugin/ as a Claude Code skills-dir plugin (loads in place).
    Idempotent symlink ~/.claude/skills/build-loop-extensions -> <root>/plugin (only the
    plugin root; pending/ is a sibling and never linked, so it cannot load)."""
    link = Path.home() / ".claude" / "skills" / "build-loop-extensions"
    link.parent.mkdir(parents=True, exist_ok=True)
    target = plugin_dir()
    if link.is_symlink() or link.exists():
        if link.is_symlink() and link.resolve() == target.resolve():
            return {"registered": True, "link": str(link), "noop": True}
        return {"registered": False, "error": f"{link} exists and is not our symlink"}
    link.symlink_to(target)
    return {"registered": True, "link": str(link)}

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--register", action="store_true", help="Also register plugin/ as a skills-dir plugin.")
    a = p.parse_args(argv)
    out = ensure_scaffold()
    if a.register:
        out["registration"] = register_skills_dir()
    print(json.dumps(out, indent=2)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes** — `python3 tests/test_extensions_init.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/extensions_init.py tests/test_extensions_init.py
git commit scripts/extensions_init.py tests/test_extensions_init.py -m "feat(extensions): idempotent scaffold + versioned manifest"
```

---

## Task 5: `scripts/extensions_check.py` — deterministic pre-approval checks

**Files:**
- Create: `scripts/extensions_check.py`
- Test: `tests/test_extensions_check.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extensions_check.py
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_check import check_skill  # noqa: E402

def _skill(tmp, name, desc, body="ok"):
    p = Path(tmp) / name / "SKILL.md"; p.parent.mkdir(parents=True)
    p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n"); return p

class CheckTests(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory()
    def tearDown(self): self.tmp.cleanup()

    def test_passes_clean_namespaced_skill(self):
        p = _skill(self.tmp.name, "ext-alice-lint-yaml", "lint YAML files on save")
        self.assertEqual(check_skill(p, core_descriptions=[]), [])

    def test_flags_missing_namespace(self):
        p = _skill(self.tmp.name, "lint-yaml", "lint YAML")
        codes = [i["code"] for i in check_skill(p, core_descriptions=[])]
        self.assertIn("namespace", codes)

    def test_flags_pii(self):
        p = _skill(self.tmp.name, "ext-alice-x", "skill for /Users/alice/secret stuff")
        self.assertIn("privacy", [i["code"] for i in check_skill(p, core_descriptions=[])])

    def test_flags_missing_frontmatter(self):
        p = Path(self.tmp.name) / "ext-alice-y" / "SKILL.md"; p.parent.mkdir(parents=True)
        p.write_text("no frontmatter here")
        self.assertIn("schema", [i["code"] for i in check_skill(p, core_descriptions=[])])
```

- [ ] **Step 2: Run to verify it fails** — `python3 tests/test_extensions_check.py -v` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_check.py — deterministic pre-approval checks for a learned skill."""
from __future__ import annotations
import re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from privacy import scan_file, load_default_patterns  # noqa: E402

NS = re.compile(r"^ext-[a-z0-9]+-")

def _frontmatter(text: str) -> dict | None:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m: return None
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1); fm[k.strip()] = v.strip()
    return fm

def check_skill(skill_md: Path, core_descriptions: list[str]) -> list[dict]:
    """Return [{code, detail}]. Empty = clean. codes: schema|namespace|privacy|trigger-overlap."""
    issues: list[dict] = []
    text = skill_md.read_text(errors="ignore")
    fm = _frontmatter(text)
    if not fm or "name" not in fm or "description" not in fm:
        issues.append({"code": "schema", "detail": "missing/invalid frontmatter (need name + description)"})
        return issues
    if not NS.match(fm["name"]):
        issues.append({"code": "namespace", "detail": f"name must match ext-<slug>-... got {fm['name']!r}"})
    for hit in scan_file(skill_md, load_default_patterns()):
        issues.append({"code": "privacy", "detail": f"deny-pattern hit: {hit['match']!r}"})
    desc_words = set(re.findall(r"[a-z]{4,}", fm["description"].lower()))
    for core in core_descriptions:
        overlap = desc_words & set(re.findall(r"[a-z]{4,}", core.lower()))
        if len(overlap) >= 4:
            issues.append({"code": "trigger-overlap", "detail": f"high description overlap with a core skill: {sorted(overlap)[:6]}"})
            break
    return issues
```

- [ ] **Step 4: Run to verify it passes** — `python3 tests/test_extensions_check.py -v` → PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/extensions_check.py tests/test_extensions_check.py
git commit scripts/extensions_check.py tests/test_extensions_check.py -m "feat(extensions): pre-approval checks (schema, namespace, privacy, trigger-overlap)"
```

---

## Task 6: `scripts/extensions_approve.py` — list + approve (pending → plugin)

**Files:**
- Create: `scripts/extensions_approve.py`
- Test: `tests/test_extensions_approve.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extensions_approve.py
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_init import ensure_scaffold  # noqa: E402
from extensions_approve import list_pending, approve  # noqa: E402

class ApproveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.tmp.name
        ensure_scaffold(git_init=False)
        d = Path(self.tmp.name) / "pending" / "skills" / "ext-alice-lint"; d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: ext-alice-lint\ndescription: lint YAML on save\n---\nok\n")
    def tearDown(self): del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]; self.tmp.cleanup()

    def test_list_shows_pending(self):
        self.assertIn("ext-alice-lint", list_pending())

    def test_approve_moves_to_plugin(self):
        res = approve("ext-alice-lint", core_descriptions=[])
        self.assertTrue(res["approved"])
        r = Path(self.tmp.name)
        self.assertTrue((r / "plugin" / "skills" / "ext-alice-lint" / "SKILL.md").exists())
        self.assertFalse((r / "pending" / "skills" / "ext-alice-lint").exists())

    def test_approve_blocked_by_checks(self):
        bad = Path(self.tmp.name) / "pending" / "skills" / "no-namespace"; bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("---\nname: no-namespace\ndescription: x\n---\n")
        res = approve("no-namespace", core_descriptions=[])
        self.assertFalse(res["approved"]); self.assertTrue(res["issues"])
```

- [ ] **Step 2: Run to verify it fails** — `python3 tests/test_extensions_approve.py -v` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_approve.py — list pending drafts; approve one (checks → move into plugin/)."""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extensions_paths import pending_dir, plugin_dir  # noqa: E402
from extensions_check import check_skill  # noqa: E402

def list_pending() -> list[str]:
    d = pending_dir() / "skills"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

def approve(name: str, core_descriptions: list[str] | None = None) -> dict:
    src = pending_dir() / "skills" / name
    if not (src / "SKILL.md").exists():
        return {"approved": False, "issues": [{"code": "missing", "detail": f"no pending skill {name!r}"}]}
    issues = check_skill(src / "SKILL.md", core_descriptions or [])
    if issues:
        return {"approved": False, "issues": issues}
    dst = plugin_dir() / "skills" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))   # git mv equivalent; the dir is inside the user's git repo
    return {"approved": True, "moved_to": str(dst)}

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true"); p.add_argument("name", nargs="?")
    a = p.parse_args(argv)
    if a.list or not a.name:
        print(json.dumps({"pending": list_pending()}, indent=2)); return 0
    res = approve(a.name)
    print(json.dumps(res, indent=2)); return 0 if res.get("approved") else 3

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes** — `python3 tests/test_extensions_approve.py -v` → PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/extensions_approve.py tests/test_extensions_approve.py
git commit scripts/extensions_approve.py tests/test_extensions_approve.py -m "feat(extensions): approve command (checks-gated pending->plugin move)"
```

---

## Task 7: Consumer routing + pending-drafts nudge

**Files:**
- Create: `scripts/extensions_route.py`, `scripts/extensions_pending_count.py`, `hooks/session-start-extensions.sh`
- Modify: `hooks/hooks.json`, `skills/build-loop/references/phase-6-learn.md`
- Test: `tests/test_extensions_route.py`

- [ ] **Step 1: Write the failing test (router)**

```python
# tests/test_extensions_route.py
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_init import ensure_scaffold  # noqa: E402
from extensions_route import route_draft  # noqa: E402
from extensions_pending_count import pending_count  # noqa: E402

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.tmp.name
        ensure_scaffold(git_init=False)
    def tearDown(self): del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]; self.tmp.cleanup()

    def test_routes_draft_into_pending_and_counts(self):
        dst = route_draft("ext-alice-lint", "---\nname: ext-alice-lint\ndescription: lint\n---\nok\n")
        self.assertTrue(Path(dst).exists())
        self.assertIn("pending", dst)
        self.assertEqual(pending_count(), 1)
```

- [ ] **Step 2: Run to verify it fails** — `python3 tests/test_extensions_route.py -v` → FAIL.

- [ ] **Step 3: Implement router + counter**

```python
# scripts/extensions_route.py
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_route.py — consumer default: place a drafted skill into pending/ (never loads until approved)."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
from extensions_init import ensure_scaffold  # noqa: E402
from extensions_paths import pending_dir  # noqa: E402

def route_draft(name: str, skill_md_text: str) -> str:
    ensure_scaffold(git_init=False)
    d = pending_dir() / "skills" / name; d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(skill_md_text)
    return str(d / "SKILL.md")

def main(argv=None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("--name", required=True); p.add_argument("--file", required=True)
    a = p.parse_args(argv); print(route_draft(a.name, Path(a.file).read_text())); return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# scripts/extensions_pending_count.py
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_pending_count.py — count pending drafts (for the session-start nudge). Fail-open to 0."""
from __future__ import annotations
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
from extensions_paths import pending_dir  # noqa: E402

def pending_count() -> int:
    d = pending_dir() / "skills"
    try:
        return sum(1 for p in d.iterdir() if p.is_dir() and (p / "SKILL.md").exists()) if d.is_dir() else 0
    except OSError:
        return 0

if __name__ == "__main__":
    print(pending_count())
```

- [ ] **Step 4: Run to verify it passes** — `python3 tests/test_extensions_route.py -v` → PASS.

- [ ] **Step 5: Add the session-start nudge hook**

```bash
# hooks/session-start-extensions.sh
#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -u
PY="$(command -v python3 || true)"; [ -z "$PY" ] && exit 0
N="$("$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/extensions_pending_count.py" 2>/dev/null || echo 0)"
[ "${N:-0}" -gt 0 ] 2>/dev/null && echo "build-loop: ${N} pending extension draft(s) await review — run: python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/extensions_approve.py\" --list"
exit 0
```
Register it: add a `SessionStart` entry in `hooks/hooks.json` invoking `bash ${CLAUDE_PLUGIN_ROOT}/hooks/session-start-extensions.sh` (mirror the shape of the existing SessionStart entries; fail-open, exit 0 always).

- [ ] **Step 6: Document the routing in `phase-6-learn.md`**

Add a bullet under the promotion step: "Consumer default — drafts route to `~/.build-loop-extensions/pending/` via `scripts/extensions_route.py --name <ext-slug> --file <draft>`; they do not load until `extensions_approve.py` moves them into `plugin/`. (Maintainer routing: P2.)"

- [ ] **Step 7: Commit**

```bash
git add scripts/extensions_route.py scripts/extensions_pending_count.py hooks/session-start-extensions.sh hooks/hooks.json skills/build-loop/references/phase-6-learn.md tests/test_extensions_route.py
git commit -m "feat(extensions): consumer draft routing + pending-drafts session-start nudge"
```

---

## Task 8: P1 integration check

- [ ] **Step 1: Full P1 test sweep** — `python3 -m unittest discover -s tests -p 'test_extensions_*.py' && python3 tests/test_privacy.py`. Expected: all PASS.
- [ ] **Step 2: End-to-end dry-run** — scaffold to a temp root, route a draft, list pending (nudge counts 1), approve it, confirm it moved to `plugin/skills/` and pending is empty. Run `self_mod_verify.py --changed-files <the new scripts>` → `verdict=pass`.
- [ ] **Step 3: Commit any fixes; tag P1 done in the plan checkboxes.**

---

## P2–P5 — outline (each becomes its own plan)

- **P2 — Identity + maintainer mode.** `~/.build-loop-identity.json` (per-machine, outside the synced repo); `extensions_route.py` reads role → consumer keeps pending/, maintainer writes a *proposal* (draft branch / `proposals/` entry) into `core_repo` behind the SAME approve gate (never an autonomous direct core write). `graduated.json` registry + a `graduate` command (move artifact → core, record old-ID→core-ID). `core_repo` validation (exists + is-a-build-loop-repo) on write and at promotion. Setup warns when a source checkout exists but no identity file.
- **P3 — Contract + update safety.** Ship `extension-api.json` in core (stable hooks/context/script surface). A post-core-update check validates each `plugin/` artifact against it + re-runs the deterministic checks; breakages demote to `pending/` and file Learn-queue repair items with evidence; the update is never blocked.
- **P4 — Hygiene surfaces.** `extensions_doctor.py`: prints instance version (core tag + extensions HEAD), trigger-overlap report vs active core skills, active-artifact inventory. Generalizability rubric added to the `promotion-reviewer` brief for the consumer PR-upstream rung (strip project nouns, synthetic out-of-domain trigger test, privacy scrub).
- **P5 — `build-loop setup`.** One idempotent first-run command consolidating P1–P4 surfaces: scaffold + git init + skills-dir registration + `install_memory.py --guided` + optional identity + printed quickstart. This is the "one-day terminal install with guidance" surface.

**Deferred (YAGNI, post-P5):** activation telemetry + auto-`prune` (to a gitignored log, not frontmatter); community-registry rung.
