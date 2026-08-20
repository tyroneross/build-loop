#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import background_item_identity as identity  # noqa: E402


def _plist(path: Path, label: str, arguments: list[str], **extra: object) -> bytes:
    payload = {"Label": label, "ProgramArguments": arguments, **extra}
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    path.write_bytes(data)
    return data


def test_display_names_are_product_and_purpose_specific() -> None:
    assert identity.display_name_for_label("ai.rosslabs.productpilot-backup") == "RossLabs ProductPilot Backup"
    assert identity.display_name_for_label("com.tyroneross.buildloop.selfreview-deep") == "Build Loop Self Review Deep"
    assert identity.display_name_for_label("com.tyroneross.mcp-watchdog") == "MCP Watchdog"


@pytest.mark.parametrize(
    "program",
    ["/bin/bash", "/bin/sh", "/usr/bin/env", "/usr/bin/python3", "/opt/bin/python3.14", "/opt/bin/node"],
)
def test_generic_interpreters_are_opaque(program: str) -> None:
    assert identity.is_generic_program(program)


def test_purpose_named_executable_is_clean() -> None:
    assert not identity.is_generic_program("/Applications/Canva.app/Contents/MacOS/Canva")
    assert not identity.is_generic_program("/Users/test/bin/Build Loop Self Review Deep")


def test_audit_counts_owned_and_non_owned_findings(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    _plist(agents / "owned.plist", "com.tyroneross.mcp-watchdog", ["/usr/bin/env", "python3", "watch.py"])
    _plist(agents / "other.plist", "org.example.worker", ["/bin/bash", "worker.sh"])
    _plist(agents / "named.plist", "com.tyroneross.named", ["/opt/tools/Named Worker"])

    result = identity.audit_directory(agents)

    assert result["generic_count"] == 2
    assert result["owned_generic_count"] == 1
    assert {item["label"] for item in result["findings"]} == {
        "com.tyroneross.mcp-watchdog",
        "org.example.worker",
    }


def test_apply_preserves_command_and_writes_recovery_manifest(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / "job.plist"
    original = _plist(
        plist_path,
        "com.tyroneross.mcp-watchdog",
        ["/usr/bin/env", "python3", "/tmp/watch.py", "--once"],
        RunAtLoad=True,
        StartInterval=60,
    )
    identity_root = tmp_path / "identity"

    result = identity.apply_directory(agents, identity_root=identity_root)

    assert result["applied_count"] == 1
    assert result["reload_performed"] is False
    assert result["audit_after"]["owned_generic_count"] == 0
    rewritten = plistlib.loads(plist_path.read_bytes())
    launcher = Path(rewritten["Program"])
    assert launcher.name == "MCP Watchdog"
    assert rewritten["ProgramArguments"] == [
        str(launcher),
        "/usr/bin/env",
        "/usr/bin/env",
        "python3",
        "/tmp/watch.py",
        "--once",
    ]
    assert rewritten["RunAtLoad"] is True
    assert rewritten["StartInterval"] == 60
    assert launcher.read_bytes() == identity.LAUNCHER_BODY
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755

    completed = subprocess.run(
        [str(launcher), "/bin/sh", "purpose-argv0", "-c", "printf %s \"$0\""],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == "purpose-argv0"

    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "applied"
    assert Path(manifest["entries"][0]["backup_path"]).read_bytes() == original


def test_apply_only_changes_owned_prefixes(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    owned = agents / "owned.plist"
    external = agents / "external.plist"
    _plist(owned, "ai.rosslabs.fleet-sweep", ["/usr/bin/env", "python3", "fleet.py"])
    external_before = _plist(external, "org.example.worker", ["/bin/bash", "worker.sh"])

    result = identity.apply_directory(agents, identity_root=tmp_path / "identity")

    assert result["applied_count"] == 1
    assert external.read_bytes() == external_before
    assert result["audit_after"]["generic_count"] == 1
    assert result["audit_after"]["owned_generic_count"] == 0


def test_restore_is_byte_for_byte(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / "job.plist"
    original = _plist(plist_path, "com.tyroneross.update-all", ["/bin/bash", "/tmp/update.sh"])
    applied = identity.apply_directory(agents, identity_root=tmp_path / "identity")

    restored = identity.restore_manifest(Path(applied["manifest_path"]))

    assert restored["restored_count"] == 1
    assert restored["reload_performed"] is False
    assert plist_path.read_bytes() == original


def test_restore_refuses_to_overwrite_post_apply_edit(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / "job.plist"
    original = _plist(plist_path, "com.tyroneross.update-all", ["/bin/bash", "/tmp/update.sh"])
    applied = identity.apply_directory(agents, identity_root=tmp_path / "identity")
    plist_path.write_bytes(plist_path.read_bytes() + b"\n")

    with pytest.raises(RuntimeError, match="restore conflict"):
        identity.restore_manifest(Path(applied["manifest_path"]))

    restored = identity.restore_manifest(Path(applied["manifest_path"]), force=True)
    assert restored["restored_count"] == 1
    assert plist_path.read_bytes() == original


def test_verify_manifest_checks_launcher_and_preserved_command(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / "job.plist"
    _plist(plist_path, "com.tyroneross.update-all", ["/bin/bash", "/tmp/update.sh"])
    applied = identity.apply_directory(agents, identity_root=tmp_path / "identity")

    verified = identity.verify_manifest(Path(applied["manifest_path"]))
    assert verified["status"] == "pass"
    assert verified["checked_count"] == 1

    rewritten = plistlib.loads(plist_path.read_bytes())
    rewritten["ProgramArguments"][-1] = "/tmp/other.sh"
    plist_path.write_bytes(plistlib.dumps(rewritten))
    failed = identity.verify_manifest(Path(applied["manifest_path"]))
    assert failed["status"] == "fail"
    assert failed["failure_count"] == 1


def test_explicit_program_is_wrapped_and_argument_tail_is_preserved(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / "job.plist"
    _plist(
        plist_path,
        "com.build-loop.worker",
        ["worker-argv0", "--flag"],
        Program="/bin/bash",
    )

    identity.apply_directory(agents, identity_root=tmp_path / "identity")

    rewritten = plistlib.loads(plist_path.read_bytes())
    assert rewritten["ProgramArguments"][1:] == ["/bin/bash", "worker-argv0", "--flag"]
    manifest = next((tmp_path / "identity" / "backups").glob("*/manifest.json"))
    assert identity.verify_manifest(manifest)["status"] == "pass"


def test_malformed_plist_is_reported_without_aborting_scan(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    # Matches the live failure class: valid plist framing with malformed XML.
    (agents / "bad.plist").write_text(
        '<?xml version="1.0"?><plist><dict><key>Label</key><string>bad & value</string></dict></plist>'
    )
    _plist(agents / "good.plist", "com.tyroneross.weekly-verify", ["/bin/bash", "verify.sh"])

    result = identity.audit_directory(agents)

    assert result["generic_count"] == 1
    assert len(result["errors"]) == 1


def test_double_hyphen_in_xml_comment_matches_plutil_tolerance(tmp_path: Path) -> None:
    data = b"""<?xml version="1.0"?>
<plist version="1.0"><dict>
<!-- running with --once is intentional -->
<key>Label</key><string>com.tyroneross.mcp-watchdog</string>
<key>ProgramArguments</key><array><string>/usr/bin/env</string><string>python3</string></array>
</dict></plist>
"""

    finding = identity.inspect_plist_bytes(data, path=str(tmp_path / "job.plist"))

    assert finding is not None
    assert finding.display_name == "MCP Watchdog"


def test_leading_newline_before_xml_declaration_matches_plutil_tolerance() -> None:
    data = b'\n<?xml version="1.0"?><plist version="1.0"><dict><key>Label</key><string>Canva</string><key>ProgramArguments</key><array><string>/Applications/Canva.app/Contents/MacOS/Canva</string></array></dict></plist>'

    assert identity.inspect_plist_bytes(data, path="canva.plist") is None
