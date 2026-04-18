"""research_packet.py — Research packet generation for build-loop.

Self-contained. No imports from external repos.

CLI usage:
  python3 scripts/research_packet.py --scan --workdir /path/to/repo --focus "auth"
  python3 scripts/research_packet.py --packet --workdir /path/to/repo --task "Add Stripe payments" --mode balanced
  python3 scripts/research_packet.py --brief --text "add some kind of payment thing"
  python3 scripts/research_packet.py --archive --workdir /path/to/repo --topic "stripe-eval" --content "packet text..."

State directory: .build-loop/research/
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXCLUDED_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
}

ENTRYPOINT_CANDIDATES = [
    "src/index.ts",
    "src/index.tsx",
    "src/main.ts",
    "src/main.tsx",
    "src/app.ts",
    "src/app.tsx",
    "src/index.js",
    "src/main.js",
    "app.py",
    "main.py",
    "manage.py",
    "src/lib.rs",
    "src/main.rs",
    "Package.swift",
]

TASK_MARKERS = {
    "product": ["product", "app", "platform", "workflow", "service"],
    "feature": ["feature", "add ", "support ", "enable ", "improve ", "versioning"],
    "algorithm": ["algorithm", "scoring", "ranking", "retrieval", "evaluation"],
    "prompt": ["prompt", "system prompt", "agent prompt", "instruction", "judge"],
    "bugfix": ["bug", "fix", "issue", "broken", "regression", "error"],
    "refactor": ["refactor", "cleanup", "restructure", "split", "migrate"],
}

STOPWORDS = {
    "about",
    "after",
    "against",
    "algorithm",
    "build",
    "building",
    "could",
    "feature",
    "into",
    "just",
    "make",
    "need",
    "please",
    "prompt",
    "repo",
    "should",
    "that",
    "this",
    "want",
    "with",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokenize_focus(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower())
    unique: list[str] = []
    for token in tokens:
        if token in STOPWORDS or token in unique:
            continue
        unique.append(token)
    return unique[:8]


# ---------------------------------------------------------------------------
# Repo detection
# ---------------------------------------------------------------------------

def detect_repo_manifests(repo_path: Path) -> list[str]:
    manifests = []
    for filename in (
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "Cargo.toml",
        "go.mod",
        "Gemfile",
        "Package.swift",
        "tsconfig.json",
        "vercel.json",
    ):
        if (repo_path / filename).exists():
            manifests.append(filename)
    return manifests


def detect_project_kind(repo_path: Path, manifests: list[str]) -> str:
    kinds = []
    if "package.json" in manifests:
        kinds.append("node")
    if {"pyproject.toml", "requirements.txt", "setup.py"} & set(manifests):
        kinds.append("python")
    if "Cargo.toml" in manifests:
        kinds.append("rust")
    if "go.mod" in manifests:
        kinds.append("go")
    if "Package.swift" in manifests:
        kinds.append("apple")
    if len(kinds) > 1:
        return "mixed"
    if kinds:
        return kinds[0]
    return "generic"


def parse_validation_commands(repo_path: Path, manifests: list[str]) -> dict[str, str]:
    commands: dict[str, str] = {}

    package_json = repo_path / "package.json"
    if "package.json" in manifests and package_json.exists():
        package_data = json.loads(package_json.read_text())
        scripts = package_data.get("scripts", {})
        for key in ("test", "lint", "build", "typecheck", "check"):
            if key in scripts:
                commands[key] = f"npm run {key}"

    pyproject = repo_path / "pyproject.toml"
    if "pyproject.toml" in manifests and pyproject.exists() and tomllib is not None:
        data = tomllib.loads(pyproject.read_text())
        project = data.get("project", {})
        optional = project.get("optional-dependencies", {})
        if "test" not in commands:
            if (repo_path / "pytest.ini").exists() or "pytest" in json.dumps(optional).lower():
                commands["test"] = "pytest"
        pyproject_text = pyproject.read_text()
        if "lint" not in commands and "ruff" in pyproject_text:
            commands["lint"] = "ruff check ."
        if "typecheck" not in commands and "mypy" in pyproject_text:
            commands["typecheck"] = "mypy ."

    if "requirements.txt" in manifests and "test" not in commands and (repo_path / "tests").exists():
        commands["test"] = "python3 -m unittest discover -s tests"

    if "Cargo.toml" in manifests:
        commands.setdefault("build", "cargo build")
        commands.setdefault("test", "cargo test")

    if "go.mod" in manifests:
        commands.setdefault("build", "go build ./...")
        commands.setdefault("test", "go test ./...")

    if "Package.swift" in manifests:
        commands.setdefault("build", "swift build")
        commands.setdefault("test", "swift test")

    return commands


def count_files(repo_path: Path, limit: int = 5000) -> Counter[str]:
    counter: Counter[str] = Counter()
    seen = 0
    for path in repo_path.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.is_file():
            counter[path.suffix or "[no_ext]"] += 1
            seen += 1
            if seen >= limit:
                break
    return counter


def probable_entrypoints(repo_path: Path) -> list[str]:
    hits = []
    for candidate in ENTRYPOINT_CANDIDATES:
        if (repo_path / candidate).exists():
            hits.append(candidate)
    return hits[:8]


def focus_hits(repo_path: Path, focus_text: str, max_hits: int = 12) -> list[str]:
    tokens = tokenize_focus(focus_text)
    if not tokens:
        return []

    rg_path = shutil.which("rg")
    if rg_path:
        pattern = "|".join(re.escape(token) for token in tokens)
        result = subprocess.run(
            [rg_path, "-n", "-S", pattern, str(repo_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        hits = [line for line in result.stdout.splitlines() if line]
        return hits[:max_hits]

    hits_list: list[str] = []
    for path in repo_path.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.parts) or not path.is_file():
            continue
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".toml"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        lowered = text.lower()
        if any(token in lowered for token in tokens):
            hits_list.append(str(path.relative_to(repo_path)))
            if len(hits_list) >= max_hits:
                break
    return hits_list


def scan_repo(repo_path: Path, focus_text: str = "") -> dict[str, Any]:
    manifests = detect_repo_manifests(repo_path)
    commands = parse_validation_commands(repo_path, manifests)
    top_dirs = [
        path.name
        for path in sorted(repo_path.iterdir())
        if path.is_dir() and path.name not in EXCLUDED_DIRS and not path.name.startswith(".")
    ][:12]
    counts = count_files(repo_path)
    return {
        "repo_path": str(repo_path),
        "project_kind": detect_project_kind(repo_path, manifests),
        "manifests": manifests,
        "top_level_dirs": top_dirs,
        "top_file_types": counts.most_common(8),
        "entrypoints": probable_entrypoints(repo_path),
        "validation_commands": commands,
        "focus_hits": focus_hits(repo_path, focus_text),
    }


# ---------------------------------------------------------------------------
# Task classification and mode inference
# ---------------------------------------------------------------------------

def classify_task(text: str) -> str:
    lowered = text.lower()
    scores: Counter[str] = Counter()
    for task_type, markers in TASK_MARKERS.items():
        for marker in markers:
            if marker in lowered:
                scores[task_type] += 1
    if not scores:
        return "feature"
    return scores.most_common(1)[0][0]


def infer_mode(text: str, default: str = "balanced") -> str:
    lowered = text.lower()
    if "--mode" in lowered:
        for mode in ("quick", "balanced", "max_accuracy"):
            if mode in lowered:
                return mode
    return default


# ---------------------------------------------------------------------------
# Integration and documentation checks
# ---------------------------------------------------------------------------

def infer_integration_topics(repo_path: Path, repo_summary: dict[str, Any], task_text: str) -> list[str]:
    lowered = task_text.lower()
    topics: list[str] = []

    if any(word in lowered for word in ("api", "apis", "sdk", "integration", "webhook", "endpoint")):
        topics.append("api")
    if any(word in lowered for word in ("auth", "login", "oauth", "session", "token", "clerk", "auth0")):
        topics.append("auth")
    if any(word in lowered for word in ("payment", "payments", "stripe", "billing", "subscription", "checkout")):
        topics.append("payments")
    if (repo_path / "vercel.json").exists() or "package.json" in repo_summary.get("manifests", []):
        package_json = repo_path / "package.json"
        if package_json.exists() and '"next"' in package_json.read_text():
            topics.append("vercel")
    if repo_summary.get("project_kind") == "apple" or list(repo_path.glob("*.xcodeproj")):
        topics.append("apple")

    unique: list[str] = []
    for topic in topics:
        if topic not in unique:
            unique.append(topic)
    return unique


def build_integration_checks(topics: list[str]) -> list[str]:
    checks = [
        "- Verify cross-file and cross-service handoffs before accepting the plan.",
        "- Confirm data contracts, state transfer, and ownership boundaries at each integration point.",
    ]
    if "api" in topics:
        checks.extend([
            "- Check request/response contracts, retries, idempotency, and timeout behavior.",
            "- Verify error handling and fallback behavior at client/server boundaries.",
        ])
    if "auth" in topics:
        checks.extend([
            "- Verify token, cookie, or session handoff across login, refresh, logout, and protected routes.",
            "- Check secrets, redirect URIs, callback handling, and deployment-specific auth behavior.",
        ])
    if "payments" in topics:
        checks.extend([
            "- Verify checkout, webhook, entitlement, and refund / retry handoffs end to end.",
            "- Confirm that payment failures degrade cleanly without breaking core user flows.",
        ])
    return checks


def build_documentation_checks(topics: list[str]) -> list[str]:
    checks = [
        "- For every integration, verify both the tool/provider documentation and the deployment/runtime documentation.",
    ]
    if "api" in topics:
        checks.append(
            "- For APIs and SDKs, verify the provider docs plus the runtime/platform docs that affect limits, auth, env vars, and networking."
        )
    if "auth" in topics:
        checks.append(
            "- For auth, verify the auth provider docs plus deployment/runtime docs for cookies, callbacks, secrets, and session persistence."
        )
    if "payments" in topics:
        checks.append(
            "- For payments, verify the payment provider docs plus deployment/runtime docs for webhooks, retries, background jobs, and secret storage."
        )
    if "vercel" in topics:
        checks.append(
            "- Check Vercel docs for env vars, function/runtime limits, webhooks, cron behavior, and edge vs node constraints."
        )
    if "apple" in topics:
        checks.append(
            "- Check Apple platform docs for capabilities, entitlements, sign-in flows, permissions, and deployment constraints."
        )
    return checks


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def confidence_bucket(score: int) -> str:
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def build_confidence_report(
    repo_summary: dict[str, Any],
    mode: str,
    task_type: str,
    include_external: bool,
) -> dict[str, str]:
    context_score = 0
    verification_score = 0
    evidence_score = 0

    if repo_summary.get("manifests"):
        context_score += 1
        evidence_score += 1
    if repo_summary.get("entrypoints"):
        context_score += 1
    if repo_summary.get("focus_hits"):
        context_score += 2
        evidence_score += 1
    if repo_summary.get("validation_commands"):
        verification_score += 3
        evidence_score += 1
    else:
        verification_score += 1

    if include_external:
        evidence_score += 1
    if mode == "max_accuracy":
        verification_score += 1

    novelty_risk = "high" if task_type in {"product", "algorithm"} else "medium"
    if task_type in {"bugfix", "refactor"}:
        novelty_risk = "low"

    report = {
        "Context coverage": confidence_bucket(context_score),
        "Verification coverage": confidence_bucket(verification_score),
        "Novelty risk": novelty_risk,
        "Evidence quality": confidence_bucket(evidence_score),
    }

    low_count = sum(
        1 for key, value in report.items()
        if key != "Novelty risk" and value == "low"
    )
    component_values = [
        report["Context coverage"],
        report["Verification coverage"],
        report["Evidence quality"],
    ]
    overall = "high"
    if low_count >= 2 or report["Context coverage"] == "low":
        overall = "low"
    elif low_count >= 1 or novelty_risk == "high" or any(value != "high" for value in component_values):
        overall = "medium"
    report["Overall confidence"] = overall
    return report


def build_confidence_actions(confidence: dict[str, str]) -> list[str]:
    overall = confidence["Overall confidence"]
    if overall == "high":
        return [
            "- Spot-check at least one critical assumption before treating the packet as final.",
            "- Re-calibrate confidence downward if the spot check fails or uncovers hidden complexity.",
        ]
    if overall == "medium":
        return [
            "- Iterate on the weakest low/medium areas before accepting the packet.",
            "- Re-run the confidence check after refining the plan, docs, or verification path.",
        ]
    return [
        "- Do another repo/documentation pass before accepting the packet.",
        "- Reduce scope, simplify the approach, or gather missing evidence, then re-calibrate confidence.",
    ]


# ---------------------------------------------------------------------------
# Brief optimization
# ---------------------------------------------------------------------------

def optimize_brief_text(raw_text: str) -> str:
    text = normalize_whitespace(raw_text)
    task_type = classify_task(text)
    suggested_mode = infer_mode(text)
    missing: list[str] = []

    lowered = text.lower()
    if "/" not in text and "repo" not in lowered and "path" not in lowered:
        missing.append("repo path or target codebase context")
    if not any(word in lowered for word in ("constraint", "deadline", "performance", "security", "cost", "latency")):
        missing.append("constraints or tradeoff priorities")
    if not any(word in lowered for word in ("test", "verify", "validation", "acceptance")):
        missing.append("verification or acceptance criteria")
    if not any(word in lowered for word in ("markdown", "plan", "brief", "report", "prompt", "artifact")):
        missing.append("desired output artifact")
    if "quick" not in lowered and "balanced" not in lowered and "max_accuracy" not in lowered:
        missing.append("desired effort / accuracy mode")
    if not any(word in lowered for word in ("faster", "accur", "smooth", "simple", "ux", "user experience")):
        missing.append("the exact user-experience improvement being targeted")

    bottom_line = f"Turn this into a `{task_type}` brief with a clear goal, repo context, verification path, and the simplest viable approach."
    sharpened = (
        f"Create a research-backed `{task_type}` packet for: {text} "
        "Ground it in the target repo first, then add only the minimum external research needed."
    )
    verification = [
        "- Define the success condition in one sentence.",
        "- List the commands, assertions, or manual checks that prove the result.",
        "- Call out unknowns that would reduce confidence before execution.",
        "- Add a simplicity check to confirm the approach does not introduce avoidable complexity.",
    ]
    handoff = (
        f"Build a `{task_type}` recommendation from this request. "
        "Start with repo grounding, keep the output answer-first, prefer existing app primitives before new libraries, and end with a concrete next action."
    )

    sections = [
        "# Optimized Brief",
        "",
        "## Bottom line",
        f"- {bottom_line}",
        "",
        "## Sharpened request",
        f"- {sharpened}",
        "",
        "## Missing assumptions",
    ]
    if missing:
        sections.extend(f"- {item}" for item in missing)
    else:
        sections.append("- No obvious gaps detected by the heuristic pass.")

    sections.extend([
        "",
        "## Suggested mode",
        f"- `{suggested_mode}`",
        "",
        "## Simplicity + UX gate",
        "- State the exact UX improvement expected from the recommendation.",
        "- Reject paths that add complexity without clear user value.",
        "- Prefer building with existing app code before adding a dependency.",
        "",
        "## Verification plan",
        *verification,
        "",
        "## Handoff prompt",
        "```text",
        handoff,
        "```",
    ])
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Research packet builder
# ---------------------------------------------------------------------------

def build_research_packet(
    task_text: str,
    repo_path: Path,
    mode: str = "balanced",
    artifact_mode: str = "research_plus_plan",
    history_summary: dict[str, Any] | None = None,
) -> str:
    task = normalize_whitespace(task_text)
    task_type = classify_task(task)
    repo_summary = scan_repo(repo_path, focus_text=task)
    include_external = mode in {"balanced", "max_accuracy"} and task_type in {
        "product",
        "algorithm",
        "prompt",
        "feature",
    }
    confidence = build_confidence_report(repo_summary, mode, task_type, include_external)
    confidence_actions = build_confidence_actions(confidence)
    integration_topics = infer_integration_topics(repo_path, repo_summary, task)
    integration_checks = build_integration_checks(integration_topics)
    documentation_checks = build_documentation_checks(integration_topics)

    findings = [
        f"project kind: `{repo_summary['project_kind']}`",
        f"manifests: `{', '.join(repo_summary['manifests']) or 'none detected'}`",
        f"entrypoints: `{', '.join(repo_summary['entrypoints']) or 'not obvious yet'}`",
        f"validation commands: `{', '.join(f'{k}={v}' for k, v in repo_summary['validation_commands'].items()) or 'none detected'}`",
    ]
    if repo_summary["focus_hits"]:
        findings.append("focus hits:\n  - " + "\n  - ".join(repo_summary["focus_hits"][:6]))

    best_path = [
        f"Use `{mode}` mode with repo-first grounding before making build recommendations.",
        "Read at least two relevant files from the focus-hit set before finalizing the plan.",
        "Design the verification path before any implementation work begins.",
        "Prefer the simplest implementation that meaningfully improves user experience without degrading another UX factor.",
        "Prefer building with existing app code and tools before adding a new library.",
    ]
    if include_external:
        best_path.append(
            "Add selective external research from primary sources only if the recommendation depends on current framework, model, or benchmark behavior."
        )
    else:
        best_path.append("Skip external research unless the user explicitly asks for it or a current fact becomes blocking.")

    why_path = [
        "Repo-aware retrieval improves multi-file planning quality over single-file reasoning.",
        "Tool-using and verification-first flows reduce overconfident first-pass recommendations.",
        "A short self-debug pass catches weak assumptions before handoff.",
        "A simplicity gate keeps recommendations focused on user value rather than architectural novelty.",
    ]

    verification = []
    if repo_summary["validation_commands"]:
        verification.extend(
            f"- Run `{command}`" for command in repo_summary["validation_commands"].values()
        )
    else:
        verification.extend([
            "- Create smoke tests or assertions for the critical path.",
            "- Define a manual verification checklist with exact expected outcomes.",
        ])
    if mode == "max_accuracy":
        verification.append("- Add a final draft -> explain -> critique -> revise pass before shipping the plan.")
    verification.append("- Check each recommendation for unnecessary complexity and for any user-experience regression.")

    risks = [
        "- Repo scan is heuristic and may miss non-standard entrypoints.",
        "- External findings are not collected by the local script; they must be added by the command flow when needed.",
        "- Confidence should drop if the task depends on hidden operational context not present in the repo.",
        "- Any recommendation that needs a new library should remain provisional until a simpler in-app option is ruled out.",
    ]
    if history_summary:
        risks.append(
            f"- History profile confidence is `{history_summary['sample_size_confidence']}`, so behavior tuning should stay lightweight."
        )

    next_action = "Run focused reads on the highest-signal files and finalize the implementation plan."
    if artifact_mode == "research_only":
        next_action = "Use this packet to decide whether deeper planning or implementation is warranted."
    elif artifact_mode == "plan_only":
        next_action = "Convert the packet into the smallest decision-complete implementation plan needed for execution."

    history_line = ""
    if history_summary:
        history_line = f"\n- History-informed default loop: `{history_summary['dominant_loop']}`"

    confidence_lines = "\n".join(
        f"- {label}: `{value}`" for label, value in confidence.items()
    )

    plan_steps = [
        "Clarify the goal and success condition in one sentence.",
        "Confirm the target repo area using the focus-hit shortlist.",
        "Read the core implementation files and adjacent tests or validators.",
        "Draft the recommended approach with dependencies, risks, and validation steps.",
        "Run the simplicity + UX gate before accepting the approach.",
        "Check the integration points and handoffs before implementation starts.",
        "If the task is high novelty or externally dependent, add targeted primary-source research before finalizing.",
    ]

    handoff_prompt = (
        "Use the attached research packet as the source of truth. "
        "Start with repo grounding, keep the output answer-first, preserve the verification plan, prefer the simplest viable path, and avoid adding new libraries unless clearly justified."
    )

    sections = [
        "# Research Packet",
        "",
        "## Bottom line",
        f"- Task type: `{task_type}`",
        f"- Recommended optimization mode: `{mode}`",
        f"- Artifact mode: `{artifact_mode}`",
        f"- Recommended path: repo-first planning with explicit verification{history_line}",
        "",
        "## What I found",
        *[f"- {item}" for item in findings],
        "",
        "## External findings",
        "- Not collected by the local script.",
        "- In `balanced` and `max_accuracy` mode, add only primary-source research when current external facts materially affect the plan.",
        "",
        "## Best path",
        *[f"- {item}" for item in best_path],
        "",
        "## Why this path",
        *[f"- {item}" for item in why_path],
        "",
        "## Verification plan",
        *verification,
        "",
        "## Integration points / handoffs",
        *integration_checks,
        "",
        "## Documentation checks",
        *documentation_checks,
        "",
        "## Simplicity + UX gate",
        "- Does this recommendation make the product faster, more accurate, smoother, or simpler?",
        "- Does it avoid degrading another experience factor?",
        "- Is this the simplest path that works?",
        "- Can this be built before adding a new library?",
        "",
        "## Risks / unknowns",
        *risks,
        "",
        "## Confidence report",
        confidence_lines,
        "",
        "## Confidence action",
        *confidence_actions,
        "",
        "## Plan.md",
        *[f"{index}. {step}" for index, step in enumerate(plan_steps, start=1)],
        "",
        "## Handoff prompt.md",
        "```text",
        handoff_prompt,
        "```",
        "",
        "## Next action",
        f"- {next_action}",
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def archive_packet(
    workdir: Path,
    topic: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a packet to .build-loop/research/YYYY-MM-DD-<topic>.md.

    Prepends a JSON metadata header block so callers can parse frontmatter.
    Returns the path of the saved file.
    """
    research_dir = workdir / ".build-loop" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^A-Za-z0-9_-]", "-", topic.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    filename = f"{date.today().isoformat()}-{slug}.md"
    dest = research_dir / filename

    meta = metadata or {}
    meta.setdefault("topic", topic)
    meta.setdefault("date", date.today().isoformat())
    meta_block = "<!--\n" + json.dumps(meta, indent=2) + "\n-->\n\n"

    dest.write_text(meta_block + content)
    return dest


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Research packet generator for build-loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Scan repo and print JSON summary")
    group.add_argument("--packet", action="store_true", help="Build and print a research packet")
    group.add_argument("--brief", action="store_true", help="Optimize a brief text and print result")
    group.add_argument("--archive", action="store_true", help="Archive packet content to .build-loop/research/")

    parser.add_argument("--workdir", type=Path, default=Path.cwd(), help="Repo root (default: cwd)")
    parser.add_argument("--focus", default="", help="Focus text for --scan (used in focus_hits)")
    parser.add_argument("--task", default="", help="Full task description for --packet")
    parser.add_argument(
        "--mode",
        default="balanced",
        choices=["quick", "balanced", "max_accuracy"],
        help="Research mode for --packet",
    )
    parser.add_argument("--text", default="", help="Raw text for --brief")
    parser.add_argument("--topic", default="", help="Topic slug for --archive")
    parser.add_argument("--content", default="", help="Packet content for --archive")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()

    if args.scan:
        result = scan_repo(workdir, focus_text=args.focus)
        print(json.dumps(result, indent=2))

    elif args.packet:
        if not args.task:
            print("ERROR: --task is required with --packet", file=sys.stderr)
            sys.exit(1)
        packet = build_research_packet(
            task_text=args.task,
            repo_path=workdir,
            mode=args.mode,
        )
        print(packet)

    elif args.brief:
        text = args.text
        if not text:
            print("ERROR: --text is required with --brief", file=sys.stderr)
            sys.exit(1)
        print(optimize_brief_text(text))

    elif args.archive:
        if not args.topic:
            print("ERROR: --topic is required with --archive", file=sys.stderr)
            sys.exit(1)
        content = args.content
        if not content:
            content = sys.stdin.read()
        dest = archive_packet(workdir, topic=args.topic, content=content)
        print(str(dest))


if __name__ == "__main__":
    main()
