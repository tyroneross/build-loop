#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Generate focused-loop packs from preset specs.

Preset files use YAML-compatible JSON so this tool stays stdlib-only while the
generated loop spec remains readable YAML.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
PRESETS_DIR = SKILL_ROOT / "presets"

IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LoopBuilderError(RuntimeError):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        raise LoopBuilderError("loop id cannot be empty")
    return slug


def require_identifier(value: str, *, field: str) -> str:
    slug = slugify(value)
    if not IDENTIFIER_RE.match(slug):
        raise LoopBuilderError(f"{field} must be kebab-case: {value!r}")
    return slug


def preset_paths() -> list[Path]:
    return sorted(PRESETS_DIR.glob("*.yaml"))


def available_presets() -> list[str]:
    return [path.stem for path in preset_paths()]


def load_preset(name: str) -> dict[str, Any]:
    preset_id = require_identifier(name, field="preset")
    path = PRESETS_DIR / f"{preset_id}.yaml"
    if not path.is_file():
        choices = ", ".join(available_presets()) or "(none)"
        raise LoopBuilderError(f"unknown preset {preset_id!r}; available: {choices}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoopBuilderError(f"invalid preset JSON in {path}: {exc}") from exc
    validate_preset(data, source=path)
    return data


def validate_preset(data: dict[str, Any], *, source: Path | None = None) -> None:
    required = ("id", "title", "summary", "inputs", "outputs", "phases", "validators", "gates", "skill_chain", "learn")
    missing = [key for key in required if key not in data]
    if missing:
        label = str(source) if source else data.get("id", "<preset>")
        raise LoopBuilderError(f"{label} missing required fields: {', '.join(missing)}")
    require_identifier(str(data["id"]), field="preset id")
    if not isinstance(data["phases"], dict) or not data["phases"]:
        raise LoopBuilderError(f"{data['id']} phases must be a non-empty object")
    if not isinstance(data["validators"], list) or not data["validators"]:
        raise LoopBuilderError(f"{data['id']} validators must be a non-empty list")
    if not isinstance(data["skill_chain"], dict):
        raise LoopBuilderError(f"{data['id']} skill_chain must be an object")


def scalar_to_yaml(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def to_yaml(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}"
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                if not item:
                    lines.append(f"{pad}{key}: {to_yaml(item, 0).strip()}")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {scalar_to_yaml(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return pad + "[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {scalar_to_yaml(item)}")
        return "\n".join(lines)
    return pad + scalar_to_yaml(value)


def build_loop_spec(preset: dict[str, Any], *, loop_id: str, title: str | None, preset_name: str) -> dict[str, Any]:
    spec = copy.deepcopy(preset)
    spec["schema_version"] = 1
    spec["id"] = loop_id
    spec["source_preset"] = preset_name
    if title:
        spec["title"] = title
    return spec


def render_loop_yaml(spec: dict[str, Any]) -> str:
    header = [
        "# Generated focused-loop spec.",
        "# Edit the artifact contract, validators, and skill_chain before using on high-stakes work.",
        "",
    ]
    return "\n".join(header) + to_yaml(spec) + "\n"


def render_rubric(spec: dict[str, Any]) -> str:
    lines = [
        f"# {spec['title']} Rubric",
        "",
        spec["summary"],
        "",
        "## Binary Validators",
        "",
        "| Validator | Pass Condition | Method |",
        "|---|---|---|",
    ]
    for validator in spec["validators"]:
        lines.append(
            f"| `{validator['id']}` | {validator['pass_condition']} | {validator.get('method', 'review')} |"
        )
    lines.extend(
        [
            "",
            "## Confirmation Gates",
            "",
        ]
    )
    for gate_name, gate_items in spec.get("gates", {}).items():
        lines.append(f"### {gate_name}")
        lines.append("")
        for item in gate_items:
            lines.append(f"- `{item}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_report_template(spec: dict[str, Any]) -> str:
    validator_rows = "\n".join(f"| `{item['id']}` | pass/fail | evidence |" for item in spec["validators"])
    outputs = "\n".join(f"- {item}" for item in spec["outputs"])
    return f"""# {spec['title']} Report

## Bottom Line

[One sentence: what changed, what decision/artifact is ready, and what remains open.]

## Outputs

{outputs}

## Evidence

- [source path / transcript timestamp / row or slide reference]

## Validator Results

| Validator | Verdict | Evidence |
|---|---|---|
{validator_rows}

## Gates

- External send/publish: [clear/confirm]
- Sensitive data exposure: [clear/confirm]
- Irreversible source-of-truth change: [clear/confirm]

## Learn

- Reusable artifact:
- Source quirks:
- Rubric failures:
"""


def render_validator(spec: dict[str, Any]) -> str:
    required_tokens = ["schema_version", "id", "phases", "validators", "gates", "skill_chain", "learn"]
    token_literal = repr(required_tokens)
    return f'''#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Basic generated validator for the {spec["id"]} loop pack."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = [
    ROOT / "loop.yaml",
    ROOT / "rubric.md",
    ROOT / "templates" / "report.md",
]
REQUIRED_TOKENS = {token_literal}


def main() -> int:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.is_file()]
    if missing:
        print("missing required files: " + ", ".join(missing), file=sys.stderr)
        return 1
    text = (ROOT / "loop.yaml").read_text(encoding="utf-8")
    absent = [token for token in REQUIRED_TOKENS if token + ":" not in text]
    if absent:
        print("loop.yaml missing required fields: " + ", ".join(absent), file=sys.stderr)
        return 1
    print("loop pack ok: " + str(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def write_file(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def create_loop(
    *,
    loop_id_raw: str,
    preset_name: str,
    output: Path | None,
    title: str | None,
    force: bool,
) -> Path:
    loop_id = require_identifier(loop_id_raw, field="loop id")
    preset_id = require_identifier(preset_name, field="preset")
    preset = load_preset(preset_id)
    spec = build_loop_spec(preset, loop_id=loop_id, title=title, preset_name=preset_id)
    target = output.expanduser().resolve() if output else (Path.cwd() / ".build-loop" / "loops" / loop_id).resolve()
    if target.exists():
        if not force:
            raise LoopBuilderError(f"target already exists: {target}; pass --force to replace")
        shutil.rmtree(target)
    write_file(target / "loop.yaml", render_loop_yaml(spec))
    write_file(target / "rubric.md", render_rubric(spec))
    write_file(target / "templates" / "report.md", render_report_template(spec))
    write_file(target / "validators" / "validate_loop.py", render_validator(spec), executable=True)
    write_file(
        target / "README.md",
        f"# {spec['title']}\n\nGenerated from `{preset_id}`. Start with `loop.yaml`, then run `python3 validators/validate_loop.py`.\n",
    )
    return target


def cmd_list(_args: argparse.Namespace) -> int:
    for name in available_presets():
        preset = load_preset(name)
        print(f"{name}\t{preset['title']}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    preset = load_preset(args.preset)
    print(render_loop_yaml(build_loop_spec(preset, loop_id=preset["id"], title=None, preset_name=preset["id"])))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    target = create_loop(
        loop_id_raw=args.loop_id,
        preset_name=args.preset,
        output=Path(args.output) if args.output else None,
        title=args.title,
        force=args.force,
    )
    print(f"created loop pack: {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List available loop presets.")
    list_parser.set_defaults(func=cmd_list)

    inspect_parser = sub.add_parser("inspect", help="Print a preset as generated loop YAML.")
    inspect_parser.add_argument("preset")
    inspect_parser.set_defaults(func=cmd_inspect)

    create_parser = sub.add_parser("create", help="Create a focused-loop pack.")
    create_parser.add_argument("loop_id")
    create_parser.add_argument("--preset", required=True, help="Preset name from `list`.")
    create_parser.add_argument("--output", help="Target directory. Defaults to .build-loop/loops/<loop-id>.")
    create_parser.add_argument("--title", help="Override generated title.")
    create_parser.add_argument("--force", action="store_true", help="Replace an existing target directory.")
    create_parser.set_defaults(func=cmd_create)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LoopBuilderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
