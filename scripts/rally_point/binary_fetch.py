# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Fetch-on-install of the pinned canonical Rust ``rally`` binary.

Build-loop's coordination facade delegates to the Rust ``rally`` binary
(agent-rally-point). When no system / sibling-checkout / PATH binary is found,
this module fetches the host-platform prebuilt asset from the PINNED release,
verifies it against the published ``.sha256`` sidecar, strips the macOS
quarantine xattr (downloaded assets are quarantined; a bundled-in-clone binary
is not), caches it, and pins to the exact version. Shipped-prebuilt-via-download
is the NORMAL provisioning path — build-loop does NOT commit ~28 MB of binaries
into git, and does NOT build from source here.

Pinned to ``PINNED_TAG``. The fetched binary must report exactly
``PINNED_VERSION`` (``rally version`` → ``rally 0.1.3+<sha>``); a binary whose
version line does not match is rejected and re-fetched.

Supported hosts (assets published for v0.1.3):
  aarch64-apple-darwin · x86_64-unknown-linux-gnu · aarch64-unknown-linux-gnu

An UNSUPPORTED host (no matching asset — e.g. x86_64-apple-darwin, musl/Alpine,
old glibc) yields ``None`` so the caller surfaces a loud ``coordination_
unavailable`` — NEVER a policy mirror.

Security: the asset is SHA256-verified BEFORE chmod+exec. FAIL-CLOSED — a
mismatch OR an unverifiable download (no checksum, no sum tool) is rejected and
never executed. The same-repo sidecar defends transit/CDN corruption + partial
downloads; it does NOT defend a GitHub-account compromise (out-of-band
``gh attestation verify`` covers that, not this fail-open path).
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# --- Pin -------------------------------------------------------------------
PINNED_TAG = "v0.1.3"
PINNED_VERSION = "0.1.3"
GH_REPO = "tyroneross/agent-rally-point"
_RELEASE_BASE = f"https://github.com/{GH_REPO}/releases/download/{PINNED_TAG}"

# uname (system:machine) → release asset triple. Hosts absent here are
# unsupported (loud coordination_unavailable, never a mirror).
_TRIPLE_BY_HOST: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "aarch64-apple-darwin",
    ("Darwin", "aarch64"): "aarch64-apple-darwin",
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("Linux", "amd64"): "x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
    ("Linux", "arm64"): "aarch64-unknown-linux-gnu",
}

_DOWNLOAD_TIMEOUT_S = 120
_SHA_TIMEOUT_S = 15
_VERSION_PROBE_TIMEOUT_S = 5


def host_triple() -> str | None:
    """Return the release asset triple for this host, or None if unsupported."""
    return _TRIPLE_BY_HOST.get((platform.system(), platform.machine()))


def cache_dir() -> Path:
    """Build-loop-namespaced runtime cache for the fetched rally binary.

    ``$XDG_CACHE_HOME/build-loop/rally`` (or ``~/.cache/build-loop/rally``).
    Separate from agent-rally-point's own ``~/.cache/rally`` so the two never
    collide.
    """
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "build-loop" / "rally"


def cached_binary_path() -> Path:
    """Pinned cache path for the fetched binary (``rally-<tag>``)."""
    return cache_dir() / f"rally-{PINNED_TAG}"


def _binary_version(binary: Path) -> str | None:
    """Return the ``X.Y.Z`` version a binary reports, or None on any failure."""
    try:
        proc = subprocess.run(
            [str(binary), "version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # "rally 0.1.3+7e33d5a" → "0.1.3"
    text = (proc.stdout or proc.stderr).strip()
    for token in text.replace("rally", "").split():
        head = token.split("+", 1)[0]
        if head and head[0].isdigit():
            return head
    return None


def version_matches_pin(binary: Path) -> bool:
    """True iff ``binary`` reports exactly the pinned version. Refuse otherwise."""
    return _binary_version(binary) == PINNED_VERSION


def _http_get(url: str, timeout: int) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — https release URL
            if getattr(resp, "status", 200) != 200:
                return None
            return resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _expected_sha256(triple: str) -> str | None:
    """Fetch + parse the published ``<asset>.sha256`` sidecar. None = unverifiable."""
    raw = _http_get(f"{_RELEASE_BASE}/rally-{triple}.sha256", _SHA_TIMEOUT_S)
    if not raw:
        return None
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return None
    # Sidecar is "<hex>  <filename>" (or just "<hex>").
    candidate = text.split()[0].lower()
    return candidate if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate) else None


def _strip_quarantine(path: Path) -> None:
    """Remove the macOS com.apple.quarantine xattr (downloaded assets get it).

    A bundled-in-clone binary is not quarantined, but a curl/urllib/browser
    download IS — Gatekeeper blocks exec until the xattr is cleared. ad-hoc
    signing (cargo default) suffices for the local-exec path once quarantine is
    gone. No-op (and never raises) when the xattr is absent or on non-macOS.
    """
    if platform.system() != "Darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-d", "com.apple.quarantine", str(path)],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def ensure_binary(*, force: bool = False) -> Path | None:
    """Return a path to a verified, version-pinned rally binary, fetching if needed.

    Order:
      1. Use the cached binary if it exists, is executable, and matches the pin.
      2. Otherwise download the host asset, verify sha256 (fail-closed), strip
         quarantine, chmod+exec, version-pin, and cache it.
      3. Unsupported host or any failure → None (caller surfaces loud
         coordination_unavailable; NEVER a policy mirror).

    ``force=True`` re-fetches even if a valid cached binary exists (used when a
    cached binary fails the version pin).
    """
    triple = host_triple()
    if triple is None:
        return None  # unsupported host → loud no-coordination upstream

    dest = cached_binary_path()
    if not force and dest.is_file() and os.access(dest, os.X_OK) and version_matches_pin(dest):
        return dest

    payload = _http_get(f"{_RELEASE_BASE}/rally-{triple}", _DOWNLOAD_TIMEOUT_S)
    if not payload:
        return None

    # FAIL-CLOSED verify BEFORE writing anything executable.
    want = _expected_sha256(triple)
    if want is None:
        return None  # unverifiable → reject (never exec an unverified download)
    got = hashlib.sha256(payload).hexdigest()
    if got != want:
        return None  # mismatch → reject (transit/CDN corruption or tamper)

    cache_dir().mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_bytes(payload)
        tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _strip_quarantine(tmp)
        if not version_matches_pin(tmp):
            tmp.unlink(missing_ok=True)
            return None  # downloaded binary is not the pinned version → reject
        os.replace(str(tmp), str(dest))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return dest


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="Fetch the pinned rally binary.")
    p.add_argument("--force", action="store_true", help="Re-fetch even if cached.")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--print-pin",
        action="store_true",
        help="Print PINNED_VERSION only (no fetch, no network) and exit. "
        "Used by the session-start on-PATH-vs-pin staleness guard.",
    )
    args = p.parse_args(argv)

    if args.print_pin:
        print(PINNED_VERSION)
        return 0

    triple = host_triple()
    path = ensure_binary(force=args.force)
    result = {
        "pinned_tag": PINNED_TAG,
        "pinned_version": PINNED_VERSION,
        "host_triple": triple,
        "supported_host": triple is not None,
        "binary": str(path) if path else None,
        "ok": path is not None,
    }
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        if path:
            print(f"rally {PINNED_VERSION} ready: {path}")
        elif triple is None:
            print(
                f"unsupported host {platform.system()}/{platform.machine()} — "
                f"no {PINNED_TAG} asset; coordination unavailable",
                file=sys.stderr,
            )
        else:
            print(f"fetch failed for {triple}@{PINNED_TAG}", file=sys.stderr)
    return 0 if path else 1


if __name__ == "__main__":
    sys.exit(_main())
