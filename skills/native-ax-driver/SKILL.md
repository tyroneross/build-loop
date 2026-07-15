---
name: build-loop:native-ax-driver
description: Use when the build needs to automate a macOS .app without touching the hardware cursor, or the user asks to "click through the app" or "test the UI headlessly". Drives running apps via Accessibility API; self-contained Swift binary — no IBR, Playwright, or Appium required.
version: 1.1.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Native AX Driver

**Built-in capability**, not a bridge. Build-loop ships its own Swift binary and Python launcher for navigating macOS apps via the Accessibility API. When this skill loads, the orchestrator can read the AX tree of any running `.app` and dispatch actions on individual elements **while the user keeps using their cursor for something else**.

## Why this exists

Build-loop's Iterate phase needs to verify native macOS UI fixes the same way it verifies web routes. Previously, build-loop deferred to IBR's MCP for native automation, which made native verification IBR-dependent. This skill lifts the needed capability into build-loop directly: same Swift code lineage, same AX actions, no MCP hop, no plugin requirement.

Build-loop still routes to IBR as the primary verifier when `skills/ibr-bridge/SKILL.md` detects it, because IBR can add session management, baselines, and screenshots as auxiliary evidence. This skill is the native fallback path when IBR is absent.

## What "cursor-free" means

The Swift binary calls `AXUIElementPerformAction` and `AXUIElementSetAttributeValue` on the resolved element directly. It does **not** invoke `CGEventCreateMouseEvent`, `CGEventPost`, or `IOHIDPostEvent`. Concretely:

| Path | API | Cursor moves? |
|---|---|---|
| **This skill** | macOS Accessibility (`AXUIElement*`) | ❌ Never |
| Virtual HID fallback (NOT bundled) | Quartz Event Services / IOKit HID | ✅ Yes |
| AppleScript "click at {x,y}" | CGEvent under the hood | ✅ Yes |

When an element has no AX action handler (rare in SwiftUI/AppKit; common in custom Metal/canvas surfaces), the driver returns `Element not found at path` or an action-specific error rather than secretly falling through to mouse synthesis. This is intentional — a missing AX handler is usually a real accessibility bug in the app being tested, and papering over it hides the defect.

## What the driver supports

Actions exposed by `python3 scripts/native_driver.py action`:

| Action | What it does | macOS AX constant |
|---|---|---|
| `press` | Click a button, activate a control | `kAXPressAction` |
| `setValue` | Set a text-field / slider / picker value (requires `--value`) | `AXSetAttributeValue(kAXValueAttribute, …)` |
| `increment` | Step a stepper / slider up | `kAXIncrementAction` |
| `decrement` | Step a stepper / slider down | `kAXDecrementAction` |
| `showMenu` | Open a contextual / popup menu | `kAXShowMenuAction` |
| `confirm` | Default button activation in a dialog | `kAXConfirmAction` |
| `cancel` | Cancel-button activation in a dialog | `kAXCancelAction` |
| `focus` | Move keyboard focus to the element | `AXSetAttributeValue(kAXFocusedAttribute, true)` |
| `scrollToVisible` | Scroll an element into the visible viewport | `AXScrollToVisible` |

Element targeting uses an integer index path from the main window root (e.g. `0,2,1` = first child → third child → second child). The path is returned by every `scan` element under the `path` key, so the typical loop is `scan` → match by `identifier` / `title` → use that element's `path` for `action`.

## Files in this skill

```
skills/native-ax-driver/
├─ SKILL.md                                 (this file)
├─ scripts/
│  ├─ layout_fill.py                        (layout-fill / gap analyzer; stdlib only)
│  └─ native_driver.py                      (Python launcher; stdlib only)
└─ swift/bl-ax-driver/
   ├─ Package.swift                         (Swift 5.9, macOS 13+)
   └─ Sources/main.swift                    (~535 LOC, AX implementation)
```

The Swift binary compiles on first use to the **consumer project's** `.build-loop/bin/bl-ax-driver` — never inside the plugin tree. Subsequent runs reuse the cached binary; rebuild fires only if `Sources/main.swift` or `Package.swift` is newer than the cached binary.

## Prerequisites

| Prereq | How to check | Failure mode |
|---|---|---|
| `swift` on PATH | `command -v swift` | `RuntimeError: \`swift\` not found on PATH` from `ensure_binary()` |
| Xcode CLT installed | `xcode-select -p` | First `swift build` fails with missing-SDK error |
| AX permission for parent process | `python3 native_driver.py preflight` | All AX calls return `kAXErrorAPIDisabled`; binary exits with the canonical "Accessibility permission required" message |
| Target app running | `python3 native_driver.py apps` | `findMainWindow` returns nil; binary exits with "No windows found for pid …" |

The parent process needing AX permission is whichever process invoked Claude Code (Terminal, iTerm, VS Code, etc.). The driver binary itself does not need to be in the AX list — permission inherits from the parent.

## Operational protocol

### Pre-flight (run once per build-loop run with a native target)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/native-ax-driver/scripts/native_driver.py preflight
```

Exit codes: `0` AX granted · `2` AX missing · `1` osascript missing.

If `2`, surface to Iterate as a blocker rather than retrying — the user has to grant permission once in System Settings; build-loop cannot do that itself.

### Scan a running app

```bash
python3 .../native_driver.py scan --app "MyApp"      # by name (substring, case-insensitive)
python3 .../native_driver.py scan --pid 44330                # by pid
```

Stdout: `WINDOW:<id>:<WxH>:<title>` header followed by the window root's children as a JSON array of `AXExtractedElement` (`role`, `subrole`, `title`, `identifier`, `value`, `enabled`, `focused`, `actions[]`, `position`, `size`, `children[]`, `path[]`).

### Analyze layout-fill / gap findings

```bash
# Analyze a saved Swift scan JSON array
python3 .../native_driver.py analyze-layout --from-file /tmp/ax-tree.json

# Accept raw scan stdout with the leading WINDOW:<id>:<WxH>:<title> header
python3 .../native_driver.py scan --app "Easy Terminal" \
  | python3 .../native_driver.py analyze-layout --stdin

# Re-scan a running app or pid and analyze the result
python3 .../native_driver.py analyze-layout --app "Easy Terminal"
python3 .../native_driver.py analyze-layout --pid 44330
```

`analyze-layout` catches the layout bug class where a content element renders narrow and centered inside a larger container, leaving large empty gutters that can be invisible in screenshots. It reads the existing Swift scan JSON only; no Swift change or rebuild is required.

Inputs:
- `--from-file`, `--stdin`, `--pid`, or `--app` are mutually exclusive.
- `--threshold` defaults to `0.12`.
- `--min-container-px` defaults to `50`.

The analyzer returns the bridge envelope shape from `skills/ibr-bridge/SKILL.md`:

```json
{
  "status": "ran",
  "route": "native",
  "verifier": "native-ax-driver",
  "artifacts": ["stdin"],
  "verification": "native-ax-driver analyze-layout ran; found 1 layout-fill finding.",
  "findings": [
    {
      "severity": "warning",
      "category": "structure",
      "message": "layout-fill: AXSplitGroup [Main]: leading empty band 317px = 30% of container width 1074px (horizontal)",
      "finding": {
        "containerRole": "AXSplitGroup",
        "containerLabel": "Main",
        "axis": "horizontal",
        "emptyPx": 317.0,
        "emptyPct": 0.2951582867783985,
        "position": "leading",
        "containerWidth": 1074.0,
        "containerHeight": 700.0,
        "detail": "AXSplitGroup [Main]: leading empty band 317px = 30% of container width 1074px (horizontal)"
      }
    }
  ]
}
```

The Swift extractor emits absolute screen coordinates, which are correct for this analysis. Each computed value is an intra-container delta, so a constant origin offset cancels out: `firstChild.min - container.min`, `container.max - lastChild.max`, and `band / container.extent`.

### Drive an element

```bash
# Press a button at AX index path 0,2,1 in pid 44330
python3 .../native_driver.py action --pid 44330 --element-path 0,2,1 --action press

# Type into a text field
python3 .../native_driver.py action --pid 44330 --element-path 0,4,0 \
    --action setValue --value "BUILD_LOOP_TEST"

# Open the popup menu of a NSPopUpButton
python3 .../native_driver.py action --pid 44330 --element-path 0,1,3 --action showMenu
```

Stdout JSON shape: `{"success": bool, "action": "press", "error": "AXPress failed" | null}`. Exit 0 on success, 1 on AX failure, 2 on bad arguments.

### Resolve a name without AX permission

`resolve` and `apps` work without AX permission — useful for the orchestrator to confirm a freshly-launched app has actually started before the AX-gated operations.

```bash
python3 .../native_driver.py resolve --app "MyApp"
# {"pid": 44330, "name": "MyApp", "bundleIdentifier": "com.example.myapp"}

python3 .../native_driver.py apps
# [{"name": "Finder", "pid": 612, "bundleIdentifier": "com.apple.finder"}, ...]
```

## Integration with build-loop phases

| Phase | Use |
|---|---|
| **Sub-step B Validate** (Review) — when uiTarget kind = `native-macos` | Run: `preflight` → `scan` → drive critical-path actions → re-`scan` → diff. |
| **Sub-step D Coverage gaps** | Enumerate `scan` results; for any element with `actions != []` and no corresponding repo-native render/interaction coverage, write a `.build-loop/ux-queue/` entry with a proposed test step pinned by `identifier` (preferred) or `title`. |
| **Phase 5 Iterate** — `.swift` files under a macOS target | Re-launch the rebuilt `.app` (`open -b <bundleId>`); replay the failing element-path + action; if two consecutive iterations fail on the same element, escalate to root-cause-investigator with the AX path — common cause is a missing `.accessibilityIdentifier(...)` modifier. |

## When *not* to use this skill

- **Web targets** — use `ui-validator` and the host browser/screenshot tooling; this skill won't help.
- **iOS simulator** — the simulator runs on macOS, but interaction goes through `idb ui tap`, not direct AX (the simulator's AX surface is too noisy for path stability). See `reference_idb_sim_tap.md`.
- **Drag-and-drop, hover-only effects, NSTrackingArea-driven UI** — these need real `CGEvent` mouse events. Out of scope. If the feature is critical, fix the AX surface in the app under test (add `.accessibilityAction { … }`) rather than synthesizing mouse events.
- **App not yet running** — the driver does not launch apps. The orchestrator's pre-step must `open -b <bundleId>` (or `open <path/to/.app>`) and verify with `resolve` before driving.

## Failure modes & recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| Binary missing after first install | `swift build` failed silently (sandbox rejection) | `ensure_binary()` retries with `--disable-sandbox` automatically; if both fail, install Xcode Command Line Tools |
| All AX calls return `kAXErrorAPIDisabled` | Parent process not in System Settings → Privacy & Security → Accessibility | Add the process; macOS 13+ requires a fresh launch after granting |
| `Element not found at path` after a UI change | Path indices shifted | Re-`scan` and look up the element by `identifier` again — paths are not stable across UI changes |
| `AXPress failed` on a clearly-clickable button | SwiftUI view missing `.accessibilityAction` | Treat as a real bug in the app under test, not a driver bug |
| iOS simulator AX tree is empty | Simulator was scanned in macOS mode | Pass `--device-name` to scope to the simulator window |

## Self-test

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/native-ax-driver/scripts/native_driver.py preflight
python3 ${CLAUDE_PLUGIN_ROOT}/skills/native-ax-driver/scripts/native_driver.py apps | head
# pick a running app, then:
python3 ${CLAUDE_PLUGIN_ROOT}/skills/native-ax-driver/scripts/native_driver.py resolve --app "Finder"
python3 ${CLAUDE_PLUGIN_ROOT}/skills/native-ax-driver/scripts/native_driver.py scan --app "Finder" | head -40
```

If all four print sensible JSON, the skill is healthy.

## Provenance

Swift extractor ported from `interface-built-right/src/native/swift/ibr-ax-extract/Sources/main.swift`. The two copies started identical; build-loop's copy can drift independently and is not auto-synced. If a future bug is fixed in IBR's copy, port it manually and bump this skill's `version`.

Layout-fill / gap analysis in `scripts/layout_fill.py` is ported from `interface-built-right/src/native/layout-fill.ts` v1.4.0. Its fixtures mirror `interface-built-right/src/native/layout-fill.test.ts`, especially the Easy Terminal regression case: a 440px terminal centered in a 1074px container yields `emptyPx == 317`, `emptyPct ~= 0.2952`, and `position == "leading"`. The analyzer is pure Python and has no live AX dependency.
