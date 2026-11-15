"""Retired AppPulse forwarding names must not return as registered entry points."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPLACEMENTS = {
    "pre-edit-apppulse.sh": "pre-edit-rally-point.sh",
    "session-start-apppulse.sh": "session-start-rally-point.sh",
    "test_apppulse_hooks.sh": "test_rally_point_hooks.sh",
}


@pytest.mark.parametrize("retired,canonical", REPLACEMENTS.items())
def test_retired_alias_has_canonical_entry_and_no_registration(retired, canonical):
    assert not (ROOT / "hooks" / retired).exists()
    assert (ROOT / "hooks" / canonical).is_file()
    for manifest in ("hooks/hooks.json", "hooks/codex.json", ".codex/hooks.json"):
        hooks = json.loads((ROOT / manifest).read_text())
        assert retired not in json.dumps(hooks)
