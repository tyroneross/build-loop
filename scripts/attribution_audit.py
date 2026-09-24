#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""attribution_audit.py — assess, apply and file the owner's credit-link standard.

The four legal layers (NOTICE, SPDX headers, REUSE.toml, canaries) stay with
``attribution_stamp.py`` / ``detect_attribution_layers.py``. This script adds the
DISCOVERY layer: the places where a project credits its owner so people and
search/AI engines can find the owner from the project. See
``skills/attribution-standard/SKILL.md`` §"Discovery layer".

Identity is never hard-coded. The owner's brand, URL and GitHub accounts come
from a profile file (default ``<memory store root>/attribution-profile.json``).
No profile → every command reports ``profile_missing`` and does nothing, so a
stranger running build-loop never gets someone else's credit stamped into
their repo.

Applicability is decided per repo, per item:
  * Only repos whose GitHub ``origin`` owner is listed in the profile qualify.
    Forks, archived repos, third-party clones, linked worktrees and repos with
    no GitHub origin are ``not_applicable`` — Apache-2.0 §4(c) requires
    preserving an upstream's credit, never replacing it.
  * Public-facing items (LICENSE, NOTICE, README credit, CITATION.cff,
    package/plugin metadata, GitHub website/topics, data license) apply to
    PUBLIC repos only; a private repo gains no discovery from them.
  * The web-app footer credit applies to any web app, public or private,
    because the deployed site is public even when the repo is not.

Each gap carries a disposition:
  * ``auto``      — a local file edit ``apply`` can make safely (never commits).
  * ``planned``   — a code change a build-loop run should make and review.
  * ``decision``  — needs the owner: choosing a license, licensing data, or
                    changing public GitHub settings.

Subcommands (all print JSON)::

    attribution_audit.py assess --repo <path>
    attribution_audit.py apply  --repo <path> [--items readme-credit,citation-cff]
    attribution_audit.py file   --repo <path>      # backlog items, deduped; closes fixed ones
    attribution_audit.py sweep  --root <dir> [--root <dir>...] [--file]

Fail-closed on facts, fail-open on exit: when ``gh`` cannot answer, the repo is
``gh_unavailable`` and nothing is assessed or filed (a fork would otherwise
look like the owner's repo). Exit code is 0 unless arguments are
invalid.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent

ITEM_IDS = (
    "license",
    "notice",
    "readme-credit",
    "citation-cff",
    "package-json",
    "plugin-json",
    "github-metadata",
    "web-app-footer",
    "data-license",
)
AUTO_ITEMS = {"notice", "readme-credit", "citation-cff", "package-json", "plugin-json"}
PROVENANCE_SOURCE = "attribution-audit"
OPEN_STATUSES = ("open", "in-progress", "blocked", "deferred")
DATA_EXTS = {".csv", ".tsv", ".parquet", ".jsonl", ".ndjson"}
WEB_APP_MARKERS = (
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.ts",
    "vite.config.js",
    "astro.config.mjs",
    "svelte.config.js",
    "nuxt.config.ts",
    "remix.config.js",
)
SOURCE_SCAN_DIRS = ("app", "src", "components", "pages", "public")
SOURCE_SCAN_EXTS = {".tsx", ".jsx", ".ts", ".js", ".astro", ".svelte", ".vue", ".html", ".mdx"}
SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".venv", "venv", "__pycache__", "coverage"}

GhFetcher = Callable[[str], "dict[str, Any] | None"]


# ── profile ───────────────────────────────────────────────────────────────────

def default_profile_path() -> Path:
    env = os.environ.get("BUILDLOOP_ATTRIBUTION_PROFILE")
    if env:
        return Path(env).expanduser()
    try:
        sys.path.insert(0, str(HERE))
        import _paths

        return _paths.memory_store_root() / "attribution-profile.json"
    except Exception:
        return Path.home() / ".build-loop" / "attribution-profile.json"


def load_profile(path: Path | None) -> dict[str, Any] | None:
    p = path or default_profile_path()
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    required = ("github_owners", "brand_name", "brand_url", "copyright_holder")
    if not all(data.get(k) for k in required):
        return None
    data.setdefault("topic", re.sub(r"[^a-z0-9-]", "", data["brand_name"].lower().split()[0]))
    data.setdefault("readme_credit", f"Built by [{data['brand_name']}]({data['brand_url']})")
    data.setdefault("years", "2025-2026")
    data["github_owners"] = [o.lower() for o in data["github_owners"]]
    return data


# ── repo facts ────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def github_slug(repo: Path) -> str | None:
    url = _git(repo, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gh_repo_view(slug: str) -> dict[str, Any] | None:
    # Test seam for the hook tests, which cannot reach GitHub: a JSON file with
    # the `gh repo view` fields to return for every slug.
    fixture = os.environ.get("BUILDLOOP_ATTRIBUTION_GH_FIXTURE")
    if fixture:
        return load_json(Path(fixture))
    fields = "visibility,isFork,isArchived,homepageUrl,repositoryTopics,licenseInfo"
    try:
        out = subprocess.run(
            ["gh", "repo", "view", slug, "--json", fields], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _first(repo: Path, names: tuple[str, ...]) -> Path | None:
    for n in names:
        p = repo / n
        if p.is_file():
            return p
    return None


def _read(p: Path | None, limit: int = 400_000) -> str:
    if p is None:
        return ""
    try:
        return p.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def license_file(repo: Path) -> Path | None:
    for p in sorted(repo.glob("LICENSE*")) + sorted(repo.glob("LICENCE*")) + sorted(repo.glob("COPYING*")):
        if p.is_file() and "data" not in p.name.lower() and "docs" not in p.name.lower():
            return p
    return None


def is_apache(repo: Path) -> bool:
    text = _read(license_file(repo), 4000)
    return "Apache License" in text and "Version 2.0" in text


def is_markdown(p: Path) -> bool:
    return p.suffix.lower() in (".md", ".markdown")


def readme_file(repo: Path) -> Path | None:
    return _first(repo, ("README.md", "readme.md", "README.markdown", "README.rst", "README.txt", "README"))


APP_FRAMEWORK_MARKERS = tuple(m for m in WEB_APP_MARKERS if not m.startswith("vite."))


def is_web_app(repo: Path) -> bool:
    """A deployable site, not a library that happens to ship a demo page.

    App-framework configs (Next, Astro, Nuxt, Remix, SvelteKit) are decisive.
    A Vite config or root index.html counts only when package.json publishes
    nothing (no ``main``/``exports``/``bin``/``module``) — library mode ships
    those fields, an app does not.
    """
    if any((repo / m).is_file() for m in APP_FRAMEWORK_MARKERS):
        return True
    if not ((repo / "vite.config.ts").is_file() or (repo / "vite.config.js").is_file()
            or (repo / "index.html").is_file()):
        return False
    pkg = load_json(repo / "package.json") if (repo / "package.json").is_file() else None
    return pkg is not None and not any(pkg.get(k) for k in ("main", "exports", "bin", "module"))


def source_mentions(repo: Path, needle: str, budget: int = 4000) -> bool:
    """True when any web source file mentions ``needle`` (bounded walk)."""
    seen = 0
    for d in SOURCE_SCAN_DIRS:
        root = repo / d
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for f in filenames:
                if Path(f).suffix not in SOURCE_SCAN_EXTS:
                    continue
                seen += 1
                if seen > budget:
                    return False
                if needle in _read(Path(dirpath) / f, 200_000):
                    return True
    return False


def tracked_data_files(repo: Path) -> int:
    out = _git(repo, "ls-files", "data", "datasets", "public/data")
    return sum(1 for line in out.splitlines() if Path(line).suffix.lower() in DATA_EXTS)


def load_json(p: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── assessment ────────────────────────────────────────────────────────────────

def _item(item_id: str, status: str, disposition: str | None, title: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {
        "id": item_id,
        "status": status,  # present | missing | not_applicable
        "disposition": disposition if status == "missing" else None,
        "title": title,
        "detail": detail,
        "fix": fix,
    }


def assess(repo: Path, profile: dict[str, Any] | None, gh: GhFetcher = gh_repo_view) -> dict[str, Any]:
    repo = repo.resolve()
    result: dict[str, Any] = {"repo": str(repo), "applies": False, "reason": "", "profile": None, "items": []}
    if profile is None:
        result["reason"] = "profile_missing"
        return result
    if not (repo / ".git").exists():
        result["reason"] = "not_a_git_repo"
        return result
    if (repo / ".git").is_file():
        result["reason"] = "linked_worktree"
        return result
    slug = github_slug(repo)
    if not slug:
        result["reason"] = "no_github_origin"
        return result
    owner = slug.split("/")[0].lower()
    if owner not in profile["github_owners"]:
        result["reason"] = f"third_party_origin:{owner}"
        return result

    meta = gh(slug)
    if meta is None:
        # Without GitHub metadata a fork of someone else's project looks like
        # the owner's own repo. Assess nothing rather than risk filing into it.
        result["reason"] = "gh_unavailable"
        return result
    visibility = str(meta.get("visibility", "UNKNOWN")).lower()
    if meta.get("isFork"):
        result["reason"] = "fork"
        return result
    if meta.get("isArchived"):
        result["reason"] = "archived"
        return result

    public = visibility == "public"
    web_app = is_web_app(repo)
    pkg = load_json(repo / "package.json") if (repo / "package.json").is_file() else None
    plugin_path = repo / ".claude-plugin" / "plugin.json"
    plugin = load_json(plugin_path) if plugin_path.is_file() else None
    brand_url = profile["brand_url"]

    kinds = [k for k, on in (("web_app", web_app), ("npm_package", bool(pkg and not pkg.get("private"))),
                             ("claude_plugin", plugin is not None)) if on]
    result.update({"applies": True, "slug": slug, "visibility": visibility, "kinds": kinds})
    items: list[dict[str, Any]] = []

    def public_only(item_id: str, title: str) -> dict[str, Any] | None:
        if not public:
            return _item(item_id, "not_applicable", None, title, "private repo: no public discovery value")
        return None

    # 1. LICENSE
    t = "Add a LICENSE"
    lic = license_file(repo)
    items.append(public_only("license", t) or (
        _item("license", "present", None, t, lic.name)
        if lic
        else _item("license", "missing", "decision", t,
                   "public repo has no license, so nobody may legally reuse it",
                   "Choose a license. Code: Apache-2.0 (its NOTICE file carries credit forward).")))

    # 2. NOTICE (Apache-2.0 only)
    t = "Add a NOTICE file crediting the owner"
    gate = public_only("notice", t)
    if gate:
        items.append(gate)
    elif not is_apache(repo):
        items.append(_item("notice", "not_applicable", None, t, "NOTICE credit is an Apache-2.0 mechanism"))
    elif (repo / "NOTICE").is_file():
        items.append(_item("notice", "present", None, t, "NOTICE"))
    else:
        items.append(_item("notice", "missing", "auto", t, "Apache-2.0 §4(d) makes redistributors keep NOTICE",
                           "attribution_audit.py apply --items notice"))

    # 3. README credit
    t = f"Add a README credit line linking {brand_url}"
    gate = public_only("readme-credit", t)
    readme = readme_file(repo)
    if gate:
        items.append(gate)
    elif readme and brand_url in _read(readme, 10_000_000):
        items.append(_item("readme-credit", "present", None, t, readme.name))
    elif readme and not is_markdown(readme):
        items.append(_item("readme-credit", "missing", "planned", t,
                           f"{readme.name} is not Markdown; add the credit in its own format",
                           f"Add a credit line linking {brand_url} to {readme.name}"))
    else:
        items.append(_item("readme-credit", "missing", "auto", t,
                           "README does not link the owner" if readme else "no README",
                           "attribution_audit.py apply --items readme-credit"))

    # 4. CITATION.cff
    t = "Add CITATION.cff (GitHub 'Cite this repository')"
    gate = public_only("citation-cff", t)
    if gate:
        items.append(gate)
    elif (repo / "CITATION.cff").is_file():
        items.append(_item("citation-cff", "present", None, t, "CITATION.cff"))
    else:
        items.append(_item("citation-cff", "missing", "auto", t, "no CITATION.cff",
                           "attribution_audit.py apply --items citation-cff"))

    # 5. package.json
    t = "Set package.json homepage, author and funding"
    if pkg is None or pkg.get("private"):
        items.append(_item("package-json", "not_applicable", None, t,
                           "no package.json" if pkg is None else "package is private (unpublished)"))
    elif (gate := public_only("package-json", t)):
        items.append(gate)
    else:
        missing = [k for k in ("homepage", "author", "funding") if not pkg.get(k)]
        items.append(_item("package-json", "missing" if missing else "present", "auto", t,
                           f"missing: {', '.join(missing)}" if missing else "all set",
                           "attribution_audit.py apply --items package-json"))

    # 6. plugin.json
    t = "Set plugin.json homepage and author"
    if plugin is None:
        items.append(_item("plugin-json", "not_applicable", None, t, "not a Claude Code plugin"))
    elif (gate := public_only("plugin-json", t)):
        items.append(gate)
    else:
        author = plugin.get("author")
        missing = [k for k in ("homepage",) if not plugin.get(k)]
        if not author or (isinstance(author, dict) and not author.get("url")):
            missing.append("author.url")
        items.append(_item("plugin-json", "missing" if missing else "present", "auto", t,
                           f"missing: {', '.join(missing)}" if missing else "all set",
                           "attribution_audit.py apply --items plugin-json"))

    # 7. GitHub website + topics (public settings -> decision)
    t = f"Set the GitHub website field and add the '{profile['topic']}' topic"
    gate = public_only("github-metadata", t)
    if gate:
        items.append(gate)
    else:
        topics = [x.get("name") for x in (meta or {}).get("repositoryTopics") or [] if isinstance(x, dict)]
        gaps = []
        if not (meta or {}).get("homepageUrl"):
            gaps.append("website field empty")
        if profile["topic"] not in topics:
            gaps.append(f"topic '{profile['topic']}' missing")
        items.append(_item("github-metadata", "missing" if gaps else "present", "decision", t,
                           "; ".join(gaps) or "set",
                           f"gh repo edit {slug} --homepage <product-url-or-{brand_url}> --add-topic {profile['topic']}"))

    # 8. Web app footer credit (any visibility)
    t = f"Add a footer credit linking {brand_url} to the web app"
    if not web_app:
        items.append(_item("web-app-footer", "not_applicable", None, t, "not a web app"))
    elif source_mentions(repo, brand_url):
        items.append(_item("web-app-footer", "present", None, t, "source links the owner"))
    else:
        items.append(_item("web-app-footer", "missing", "planned", t,
                           "deployed site does not credit the owner",
                           f"Add a site-wide footer: '{profile['brand_name']}' linking {brand_url} (brand-name anchor, no keywords)"))

    # 9. Data license (public repos that publish data)
    t = "License published data under CC BY 4.0 (LICENSE-data)"
    gate = public_only("data-license", t)
    n_data = tracked_data_files(repo)
    if gate:
        items.append(gate)
    elif n_data == 0:
        items.append(_item("data-license", "not_applicable", None, t, "no tracked data files"))
    elif any((repo / n).is_file() for n in ("LICENSE-data", "LICENSE-DATA", "DATA_LICENSE")):
        items.append(_item("data-license", "present", None, t, "data license file"))
    else:
        items.append(_item("data-license", "missing", "decision", t,
                           f"{n_data} tracked data files carry no data license",
                           "Decide on CC BY 4.0 for data (never for code); add LICENSE-data"))

    result["items"] = items
    result["missing"] = [i["id"] for i in items if i["status"] == "missing"]
    return result


# ── apply (auto items only; never commits) ────────────────────────────────────

def _dump_json_like(original: str, data: dict[str, Any]) -> str:
    indent = 2
    m = re.search(r"\n([ \t]+)\"", original)
    if m:
        indent = len(m.group(1).expandtabs(4))
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    return text + ("\n" if original.endswith("\n") else "")


def apply(repo: Path, profile: dict[str, Any], only: set[str] | None = None,
          gh: GhFetcher = gh_repo_view) -> dict[str, Any]:
    report = assess(repo, profile, gh)
    changed: list[str] = []
    skipped: list[str] = []
    if not report["applies"]:
        return {"repo": str(repo), "applied": [], "reason": report["reason"]}
    repo = repo.resolve()
    brand, url, holder, years = profile["brand_name"], profile["brand_url"], profile["copyright_holder"], profile["years"]
    for item in report["items"]:
        if item["status"] != "missing" or item["disposition"] != "auto":
            continue
        if only and item["id"] not in only:
            continue
        iid = item["id"]
        if iid == "notice":
            # One NOTICE shape across build-loop: the stamper's canonical template
            # (holder + AI-assistance disclosure), plus the owner's brand link.
            sys.path.insert(0, str(HERE))
            from attribution_stamp import NOTICE_TEMPLATE

            body = NOTICE_TEMPLATE.format(repo_name=repo.name, copyright_text_no_email=f"{years} {holder}", name=holder)
            (repo / "NOTICE").write_text(f"{body}\n{brand}: {url}\n")
            changed.append("NOTICE")
        elif iid == "readme-credit":
            # Append-only: existing bytes are never read back and rewritten, so
            # size limits and encodings cannot corrupt the file.
            readme = readme_file(repo) or (repo / "README.md")
            credit = f"\n---\n\n{profile['readme_credit']}\n"
            if readme.is_file():
                with readme.open("rb") as fh:
                    size = fh.seek(0, 2)
                    if size:
                        fh.seek(size - 1)
                    ends_nl = size == 0 or fh.read(1) == b"\n"
                with readme.open("a", encoding="utf-8") as fh:
                    fh.write(("" if ends_nl else "\n") + credit)
            else:
                readme.write_text(f"# {repo.name}\n{credit}")
            changed.append(readme.name)
        elif iid == "citation-cff":
            slug = report.get("slug", "")
            lic = "Apache-2.0" if is_apache(repo) else None
            lines = [
                "cff-version: 1.2.0",
                'message: "If you use this software, please cite it as below."',
                f'title: "{repo.name}"',
                "type: software",
                "authors:",
                f'  - name: "{holder}"',
                f'    website: "{url}"',
                f'url: "{url}"',
            ]
            if slug:
                lines.append(f'repository-code: "https://github.com/{slug}"')
            if lic:
                lines.append(f"license: {lic}")
            (repo / "CITATION.cff").write_text("\n".join(lines) + "\n")
            changed.append("CITATION.cff")
        elif iid in ("package-json", "plugin-json"):
            p = repo / ("package.json" if iid == "package-json" else ".claude-plugin/plugin.json")
            raw = _read(p)
            data = load_json(p)
            if data is None:
                skipped.append(f"{iid}: unreadable JSON")
                continue
            if not data.get("homepage"):
                data["homepage"] = url
            author = data.get("author")
            if not author:
                data["author"] = {"name": holder, "url": url}
            elif isinstance(author, dict) and not author.get("url"):
                author["url"] = url
            if iid == "package-json" and not data.get("funding"):
                data["funding"] = url
            p.write_text(_dump_json_like(raw, data))
            changed.append(str(p.relative_to(repo)))
    return {"repo": str(repo), "applied": changed, "skipped": skipped}


# ── file (backlog open items) ─────────────────────────────────────────────────

def _existing_items(repo: Path) -> dict[str, tuple[str, str]]:
    """Map attribution item id -> (backlog id, status) for items this tool filed."""
    found: dict[str, tuple[str, str]] = {}
    items_dir = repo / ".build-loop" / "backlog" / "items"
    # Archive first, live items second: a re-filed live item must win over the
    # archived "done" copy of the same gap, or every push would file it again.
    for d in (items_dir.parent / "archive", items_dir):
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            text = _read(f, 6000)
            m = re.search(r"attribution:([a-z-]+)", text)
            if not m or PROVENANCE_SOURCE not in text:
                continue
            status = re.search(r"^status:\s*(\S+)", text, re.M)
            bid = re.search(r"^id:\s*(\S+)", text, re.M)
            if bid:
                found[m.group(1)] = (bid.group(1), status.group(1) if status else "open")
    return found


def _backlog(args: list[str]) -> dict[str, Any]:
    cmd = [sys.executable, str(HERE / "backlog.py"), *args]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        payload = json.loads(out.stdout or "{}")
    except ValueError:
        payload = {"raw": out.stdout[-400:]}
    payload.setdefault("ok", out.returncode == 0)
    return payload


def file_items(repo: Path, profile: dict[str, Any], gh: GhFetcher = gh_repo_view,
               backlog: Callable[[list[str]], dict[str, Any]] = _backlog) -> dict[str, Any]:
    report = assess(repo, profile, gh)
    summary: dict[str, Any] = {"repo": str(repo.resolve()), "applies": report["applies"],
                               "reason": report["reason"], "filed": [], "closed": [], "kept": []}
    if not report["applies"]:
        return summary
    repo = repo.resolve()
    _file_items(repo, report, backlog, summary)
    return summary


def _file_items(repo: Path, report: dict[str, Any], backlog: Callable[[list[str]], dict[str, Any]],
                 summary: dict[str, Any]) -> None:
    existing = _existing_items(repo)
    for item in report["items"]:
        iid = item["id"]
        prior = existing.get(iid)
        if item["status"] == "present" and prior and prior[1] in OPEN_STATUSES:
            backlog(["update", prior[0], "--repo", str(repo), "--status", "done"])
            summary["closed"].append(iid)
            continue
        if item["status"] != "missing":
            continue
        if prior and prior[1] in OPEN_STATUSES + ("dropped",):
            summary["kept"].append(iid)  # already tracked, or the owner dropped it on purpose
            continue
        disposition = item["disposition"]
        args = [
            "new", "--repo", str(repo), "--area", "attribution",
            "--type", "decision" if disposition == "decision" else "debt",
            "--title", f"Attribution: {item['title']}",
            "--priority", "P3",
            "--provenance-source", PROVENANCE_SOURCE,
            "--provenance-ref", f"attribution:{iid}",
            "--what-happened", item["detail"],
            "--recommendation", item["fix"],
            "--why", "Credit links let people and search/AI engines find the owner from the project "
                     "(skills/attribution-standard/SKILL.md, Discovery layer).",
            "--json",
        ]
        if disposition == "decision":
            args += ["--bucket", "decision", "--gated", "product-decision"]
        else:
            args += ["--bucket", "planned"]
        res = backlog(args)
        summary["filed"].append({"item": iid, "disposition": disposition, "backlog": res.get("id"), "ok": res.get("ok")})


# ── sweep ─────────────────────────────────────────────────────────────────────

def candidate_repos(roots: list[Path]) -> list[Path]:
    repos: list[Path] = []
    for root in roots:
        root = root.expanduser().resolve()
        if (root / ".git").is_dir():
            repos.append(root)
            continue
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / ".git").is_dir():
                repos.append(child)
    return repos


def sweep(roots: list[Path], profile: dict[str, Any], do_file: bool, gh: GhFetcher = gh_repo_view) -> dict[str, Any]:
    rows = []
    for repo in candidate_repos(roots):
        if do_file:
            s = file_items(repo, profile, gh)
            rows.append({"repo": repo.name, "applies": s["applies"], "reason": s["reason"],
                         "filed": [f["item"] for f in s["filed"]], "closed": s["closed"], "kept": s["kept"]})
        else:
            a = assess(repo, profile, gh)
            rows.append({"repo": repo.name, "applies": a["applies"], "reason": a["reason"],
                         "visibility": a.get("visibility"), "missing": a.get("missing", [])})
    applicable = [r for r in rows if r["applies"]]
    return {"repos_scanned": len(rows), "repos_applicable": len(applicable), "rows": rows}


# ── cli ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("assess", "apply", "file"):
        sp = sub.add_parser(name)
        sp.add_argument("--repo", type=Path, required=True)
        if name == "apply":
            sp.add_argument("--items", default="", help="comma list of auto item ids (default: all auto)")
    sw = sub.add_parser("sweep")
    sw.add_argument("--root", type=Path, action="append", required=True)
    sw.add_argument("--file", action="store_true", help="file backlog items instead of only assessing")
    args = ap.parse_args(argv)

    if os.environ.get("BUILDLOOP_ATTRIBUTION_AUDIT") == "0":
        print(json.dumps({"skipped": "BUILDLOOP_ATTRIBUTION_AUDIT=0"}))
        return 0
    profile = load_profile(args.profile)
    if profile is None:
        print(json.dumps({"reason": "profile_missing", "profile_path": str(args.profile or default_profile_path())}))
        return 0
    if args.cmd == "assess":
        out = assess(args.repo, profile)
    elif args.cmd == "apply":
        only = {x.strip() for x in args.items.split(",") if x.strip()} or None
        bad = (only or set()) - AUTO_ITEMS
        if bad:
            ap.error(f"not auto-applicable: {', '.join(sorted(bad))}")
        out = apply(args.repo, profile, only)
    elif args.cmd == "file":
        out = file_items(args.repo, profile)
    else:
        out = sweep(args.root, profile, args.file)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
