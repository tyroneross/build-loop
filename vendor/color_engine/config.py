"""Per-install configuration for the colour engine.

The engine ships as a vendored module: Groundwork holds the canonical source, and
each consumer (build-loop, ai-assistant, …) gets its own copy so it keeps working
when installed standalone. What must NOT fragment is taste — the combos registry
and the decision profile are shared by default, so a favourite recorded while
building an app is available next time any surface asks a colour question.

Resolution order for every value (first hit wins):

  1. explicit argument passed by the caller
  2. GROUNDWORK_COLOR_* environment variable
  3. nearest `color.config.json`, searching upward from `start_dir`
  4. the built-in defaults below

A consumer customises by dropping a `color.config.json` next to its vendored copy.
Only the keys it wants to change need to be present.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ── built-in defaults ────────────────────────────────────────────────────────
# The vector defaults are deliberately middle-of-the-road: an install customises
# them, an elicitation session overrides them, and neither should have to fight a
# strong opinion baked in here.
DEFAULTS: dict[str, Any] = {
    "install": "groundwork",
    "vector": {
        "energy": "balanced",
        "contrast_feel": "standard",
        "accent_intensity": "clear",
        "harmony": "analogous",
    },
    # Shared so taste accumulates across every consumer rather than per-plugin.
    "registry_dir": "~/dev/designs/.groundwork-color",
    "combos_file": "combos.jsonl",
    "profile_file": "profile.json",
    # Floors. AA body text is 4.5; raise per install, never lower.
    "contrast_floor_text": 4.5,
    "contrast_floor_ui": 3.0,
    # Solve both twins by default — an accent that passes on near-black can fail
    # badly on white, and shipping one mode untested is the most common defect.
    "require_both_twins": True,
    "surface_L_light": 0.985,
    "surface_L_dark": 0.16,
}

CONFIG_NAME = "color.config.json"
ENV_PREFIX = "GROUNDWORK_COLOR_"


def find_config(start_dir: str | Path | None = None) -> Path | None:
    """Nearest color.config.json, searching upward. None when absent."""
    here = Path(start_dir or Path(__file__).parent).resolve()
    for d in (here, *here.parents):
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(start_dir: str | Path | None = None) -> dict[str, Any]:
    """Merged config: defaults ← nearest color.config.json ← environment."""
    cfg = dict(DEFAULTS)

    path = find_config(start_dir)
    if path:
        try:
            cfg = _deep_merge(cfg, json.loads(path.read_text()))
            cfg["_config_path"] = str(path)
        except (json.JSONDecodeError, OSError) as exc:
            # A broken local config must not take colour down; the defaults are
            # valid on their own. Surface it rather than failing silently.
            cfg["_config_error"] = f"{path}: {exc}"

    for key in ("registry_dir", "install", "combos_file", "profile_file"):
        env = os.environ.get(ENV_PREFIX + key.upper())
        if env:
            cfg[key] = env

    return cfg


def registry_dir(start_dir: str | Path | None = None) -> Path:
    """Shared directory holding combos + profile. Created on demand."""
    cfg = load_config(start_dir)
    d = Path(os.path.expanduser(cfg["registry_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def combos_path(start_dir: str | Path | None = None) -> Path:
    cfg = load_config(start_dir)
    return registry_dir(start_dir) / cfg["combos_file"]


def profile_path(start_dir: str | Path | None = None) -> Path:
    cfg = load_config(start_dir)
    return registry_dir(start_dir) / cfg["profile_file"]


def default_vector(start_dir: str | Path | None = None) -> dict[str, Any]:
    """This install's starting relationship vector."""
    return dict(load_config(start_dir)["vector"])


def describe(start_dir: str | Path | None = None) -> str:
    cfg = load_config(start_dir)
    lines = [
        f"install        : {cfg['install']}",
        f"config         : {cfg.get('_config_path', '(defaults only)')}",
        f"registry       : {registry_dir(start_dir)}  (shared)",
        f"combos         : {combos_path(start_dir).name}",
        f"profile        : {profile_path(start_dir).name}",
        f"vector         : {cfg['vector']}",
        f"contrast floor : text {cfg['contrast_floor_text']} · ui {cfg['contrast_floor_ui']}",
        f"both twins     : {cfg['require_both_twins']}",
    ]
    if err := cfg.get("_config_error"):
        lines.append(f"⚠️  config error: {err}  (using defaults)")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
