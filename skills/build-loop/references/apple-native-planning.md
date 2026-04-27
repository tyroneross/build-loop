# Apple Native Planning Reference

Phase 2 (Plan) guidance for native iOS / macOS / watchOS work. Surfaces the upfront decisions that, when skipped, become Phase 5 (Iterate) emergencies.

Source: build-loop run on FlowDoro alarm-not-firing fix (2026-04-26). Generalized from the specific failure modes that bit that build.

## When this reference fires

`state.json.platform == "apple"` and the goal touches any of:
- A timed notification, alarm, reminder, or scheduled local push
- Background session continuity (Pomodoro, fasting, sleep, meditation)
- Multi-target shared engine (iOS + watchOS, iOS + macOS)
- Audio + haptic completion routing
- Focus mode, DND, Time Sensitive, or Critical Alerts behavior
- Live Activity / Dynamic Island

If ANY apply, the planner MUST resolve every required decision below before dispatching Execute subagents.

## Required upfront decisions

### 1. Targets

State the exact list. Do not assume.

- iOS app
- watchOS companion (paired? standalone? complication?)
- macOS Catalyst, native macOS, or no Mac
- Live Activity / Dynamic Island widget
- Home Screen widget (iOS), watchOS widget, macOS menu bar item

For each target chosen, the planner adds: deployment target, entitlements list, INFOPLIST keys, code-signing path.

Common trap: the "shared" Swift file is included in only one target's source list (XcodeGen `project.yml`). Build error surfaces only when that target rebuilds.

### 2. Notification strategy

Pick exactly one per use case. Mixing without intent is the root cause of "alarm doesn't fire."

| Strategy | When | Cost |
|---|---|---|
| Scheduled `UNTimeIntervalNotificationTrigger` armed at session START | Pomodoro, fasting, meditation — known duration | Need persisted `endDate`, idempotent re-arm on lifecycle events |
| Scheduled, armed at background only | Foreground-only apps where backgrounding implies "user left" | High failure rate; FlowDoro proved this |
| Reactive (`willPresent` while running) | Foreground-only flows; prefers in-app modal over banner | Doesn't survive force-quit |
| `interruptionLevel = .timeSensitive` | Most timer apps | Free; user opts in via Focus settings |
| `interruptionLevel = .critical` | Genuine alarms with Apple-approved Critical Alerts entitlement | Must apply to Apple |
| BGAppRefreshTask / silent push | Server-driven reminders | Out of scope for local timers |

The planner names the strategy in the Plan output. "Scheduled at session start with idempotent re-arm on resume + cancel on pause/reset/skip/completion" is the canonical safe answer for Pomodoro-style timers.

### 3. Background modes — what NOT to add

Do NOT add `UIBackgroundModes: [audio]` to keep a timer running. App Store rejection is near-certain unless the app is a media player, navigation app, or VOIP. Use scheduled notifications instead. State this constraint explicitly in the plan so Execute subagents don't "fix" silence by adding the mode.

### 4. Haptic strategy

Haptics are platform-split. Each surface gets its own decision.

| Surface | API | Plays through |
|---|---|---|
| iPhone | `CHHapticEngine` (rich patterns), `UINotificationFeedbackGenerator` (simple), `UIImpactFeedbackGenerator` | Taptic Engine, bypasses ringer switch |
| Apple Watch | `WKInterfaceDevice.play(_:)` | Watch haptic motor |
| iPad | `UINotificationFeedbackGenerator` only on supported models | Limited |
| macOS | None (no haptic hardware on Mac) | n/a |

CoreHaptics requires a fallback to `UINotificationFeedbackGenerator` for older devices. Audio and haptic paths must be independent — silenced audio should still haptic.

### 5. Audio routing

For chime / alarm sounds:

- `AVAudioSession` category `.playback` with `mixWithOthers` option to bypass the silent switch (still respects ringer volume). Set in app launch BEFORE any AVAudioPlayer creation.
- Bundled asset in `Bundle.main` with explicit `forResource:withExtension:` lookup.
- Always provide `UNNotificationSound.default` as fallback when the bundled asset can't resolve. Silent failure at delivery is the worst outcome.
- Do NOT use `AVAudioSession.Category.ambient` for alarms — it respects the silent switch.
- For notification sounds: `UNNotificationSound(named:)` looks up files in the app bundle automatically, NOT the Documents directory.

### 6. WatchConnectivity bridge

If iOS + watchOS, pick exactly one transport per message type, with rationale.

| Transport | Use for |
|---|---|
| `sendMessage(_:replyHandler:)` | Live, foreground-to-foreground, expects reply |
| `transferUserInfo` | FIFO queue, delivers when reachable |
| `updateApplicationContext` | Latest-state-only, replaces prior pending |
| `transferFile` | Large blobs |
| `transferCurrentComplicationUserInfo` | Complication-targeted |

Pomodoro state usually wants `updateApplicationContext` (latest state wins) for the timer status, plus `sendMessage` for explicit user actions (start/pause/stop).

### 7. Persistence for active session recovery

When the app is force-killed or crashes mid-session, recovery must work. Pick one:

- `UserDefaults` — simple state (mode, startDate, elapsed, intention). Fits Pomodoro.
- `SwiftData` / Core Data — complex relational state. Overkill for a single active session.
- File-based JSON in `Documents/` — when state is JSON-shaped and you want an audit trail.

The persisted record MUST include a wall-clock start time (not relative seconds). Recovery code computes age via `Date().timeIntervalSince(startDate)`. Add a hard cap (e.g., 90 min) past which the recovery prompt is suppressed.

### 8. Authorization timing (notifications)

Three points to choose between:

| When | Pros | Cons |
|---|---|---|
| App launch | Simple | Apple HIG discourages; users decline unexplained prompts |
| First Start tap | Best context: user committed to a session | Need pre-permission UI sheet (HIG: explain value first) |
| First completion | Worst — first session runs unauthorized and silently fails | Don't do this |

Use the first-Start pattern with a pre-permission sheet that explains why you need it.

## Test matrix templates

### iPhone (15 states)

Reuse the matrix from `claude-code-debugger:debugging-memory/references/ios-notification-alarm-playbook.md`. Phase 4 (Review) Validate sub-step references this matrix. Mark every row that requires a real device as `⚠️ device-only` in the scorecard; never claim ✅ on a sim-only verification.

### Apple Watch (11 states)

| # | Scenario | Expected |
|---|---|---|
| 1 | Foreground tick to zero | Watch haptic + sound (if not silent) |
| 2 | Wrist down, watch dimmed | Haptic delivers on wake |
| 3 | Watch app force-quit | Notification still fires (if scheduled) |
| 4 | Phone unreachable, watch standalone | Local schedule fires |
| 5 | Phone reachable, watch+phone both foreground | Single chime, no double |
| 6 | Mode switch via Watch | iPhone reflects state via WatchConnectivity |
| 7 | Pause via Watch crown | iPhone pauses, notification cancelled |
| 8 | Workout running concurrently | Watch app coexists; HRV capture ok |
| 9 | Always-on display | Tick continues, complication updates |
| 10 | Charging | Foreground completion still fires |
| 11 | watchOS Focus mode on | Banner suppressed (expected) |

## Common Apple pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Time Sensitive vs Critical Alerts | "Alarm suppressed in Focus / DND" | Time Sensitive entitlement + `interruptionLevel = .timeSensitive`. Critical Alerts requires Apple approval |
| Silent switch silences alarm | "Alarm doesn't sound" on iPhone | `AVAudioSession.Category.playback` with explicit activate |
| AVAudioSession `.ambient` for alarms | Same as above | Use `.playback` |
| Single-target Swift file in multi-target project | `cannot find 'Foo' in scope` in watchOS only | Add to project.yml watchOS sources, or use `#if !os(watchOS)` |
| `UNNotificationSetting.badgeSetting` on watchOS | Compile error | `#if os(watchOS)` guard |
| `UIImpactFeedbackGenerator` on macOS | Compile error | `#if !os(macOS)` guard |
| Notification scheduled with stale `timeInterval` after recovery | Alarm fires immediately or never | Recompute `endDate - now`, re-arm, cancel if `<= 0` |
| Live Activity stale endDate | Lock-screen countdown jumps | Update with authoritative `endDate = phaseStartDate + totalTime + pauseAccumulatedDuration` |
| TestFlight build with new entitlement but no Apple-approved Critical Alerts | Crash on launch on TestFlight | Apply for entitlement before shipping |

## Plan-output checklist

Phase 2 Plan output for an Apple-native goal should include, in order:

1. **Targets**: explicit list with deployment targets
2. **Notification strategy**: which row from §2, with rationale
3. **Background modes added**: explicit "none" if no audio/voip; otherwise list with App Store risk note
4. **Authorization moment**: which from §8
5. **Audio + haptic split**: per-surface API choice
6. **WatchConnectivity transports** (if applicable): per-message-type
7. **Persistence layer**: one of §7
8. **Test matrix subset**: which rows are sim-verified vs device-deferred
9. **Diagnostic logging**: what subsystem/category, what events
10. **Anti-pattern guard**: explicit "do NOT add UIBackgroundModes audio" if relevant

## When to escalate

If Plan can't resolve any of §1-§8 from the goal text + existing repo state, escalate to the user before Execute. These are not safe to assume; the wrong choice becomes a Phase 5 firefight.

## watchOS modernization checklist

Captured from FlowDoro build 73 (2026-04-26). Use during Phase 2 Plan when the goal touches a watchOS target — `*.appiconset` decisions, navigation refactors, or Smart Stack work. Each item is a concrete check, not a recommendation.

### Navigation

1. **Vertical `TabView(.verticalPage)` is the post-watchOS-10 canonical root.** Three peers max; first tab gets the large title. `NavigationStack` only inside a tab that drills down (e.g. a Customize tab opening a detail form). Crown rotates between tabs.
2. **Anti-pattern: horizontal page-based TabView.** Apple deprecated the visual model. If the existing root uses it, refactor before any other UI work — every other change inherits the old chrome.
3. **Anti-pattern: modal sheets stacked >1 deep.** Replace with NavigationStack push or vertical-tab swap. A watch sheet stacked on a sheet is unnavigable on a 41–49mm screen.
4. **Tab content can resize on watchOS 10+.** Useful for a "running session" tab that should expand to full-screen during an active timer — opt in by giving the running view a larger ideal size and letting the tab grow.

### Always-On Display (AOD)

5. **Read `\.isLuminanceReduced` in every view that renders accent color, filled shapes, or live private data.** Do not branch on `\.scenePhase` — AOD is a luminance state, not a lifecycle state. UI continues updating at ≤1 Hz under dim.
6. **Three required AOD adaptations:**
   - Accent → `.foregroundStyle(.secondary)` (or `HierarchicalShapeStyle.secondary` when binding to a `ShapeStyle` slot).
   - Filled `Capsule()`/`Circle()`/`RoundedRectangle().fill(...)` → `.stroke(...)` outline.
   - Hide live biometric or private readouts (heart rate, intention text, anything wrist-down strangers shouldn't see).
7. **Keep the running countdown legible under AOD.** It's the entire reason a user glances. Lower contrast is fine; hiding it is not.
8. **Type system note:** ternary across heterogeneous shape styles fails — `ShapeStyle` is not a uniform existential. Wrap with `AnyShapeStyle(HierarchicalShapeStyle.secondary)` vs `AnyShapeStyle(Color.accent)` so Swift can pick a common type.

### Liquid Glass (watchOS 26+)

9. **Use `#available(watchOS 26.0, *)` conditional, never bump deployment target.** Bumping locks out users on watchOS 11–25, which is most of the install base for ~12 months post-release.
10. **Wrap as a `ViewModifier`** so call sites stay terse:
    ```swift
    private struct GlassPrimaryStyle: ViewModifier {
        func body(content: Content) -> some View {
            if #available(watchOS 26.0, *) {
                content.buttonStyle(.glassProminent)
            } else {
                content.buttonStyle(.borderedProminent)
            }
        }
    }
    ```
11. **Only the primary action per screen gets `.glassProminent`.** Secondary actions use `.glass` (or `.bordered` on fallback). Anti-pattern: glass on every button — defeats the focal weight the material is designed to carry.
12. **Performance:** if frame drops show on Series 6/7-class hardware under glass, fall back to `.background(.ultraThinMaterial)` (available watchOS 9+) which approximates the look without the blur cost.

### Smart Stack + complications

13. **Compute `var relevance: TimelineEntryRelevance?` on every `TimelineEntry`.** Smart Stack ranks by `score` (Float, 0–100). Idle/stale → 0. Running primary state → 100. Paused → 10. Secondary phase running → 60. Without `relevance`, the system assumes 0 and the complication never surfaces.
14. **Set `duration:` to the remaining session window** (e.g. `TimeInterval(timeLeft)`). The system ages the entry out automatically when duration elapses; otherwise the stack pins a stale entry.
15. **Live Activity auto-pickup on watchOS 26+.** If iOS already starts a Live Activity via `Activity.request(...)`, no Watch-side code needed — Smart Stack surfaces it. This is the highest-leverage Watch feature for any session-based app.
16. **`supportedFamilies` checklist:** `accessoryCircular`, `accessoryCorner`, `accessoryInline`, `accessoryRectangular`. Skipping any reduces the watch faces a user can pin the complication to.
17. **ClockKit migration check:** `import ClockKit` should appear nowhere. If users had a ClockKit complication pre-watchOS-9, implement `CLKComplicationWidgetMigrator` so they auto-migrate without re-pinning. Fresh apps skip this.

### App icon

18. **Single-size 1024×1024 PNG, sRGB, no alpha.** Xcode 14+ generates per-device sizes at build. The runtime applies the circular mask — design with the center 50%-radius circle in mind, ship a square master.
19. **Separate watchOS catalog when the iOS catalog already has watch entries.** Keeps the modern `idiom: watch` 1024 entry isolated from the legacy iOS-bundled watch icon roles, and lets the watch art evolve independently.
20. **Override `ASSETCATALOG_COMPILER_APPICON_NAME` per target.** When iOS uses `AppIcon` and watchOS uses `AppIcon-Watch`, the catalogs can both ship without name collision. Set the watchOS-target setting in `project.yml` (XcodeGen) or per-target build settings.
21. **Verify post-build with `xcrun assetutil`:**
    ```bash
    xcrun assetutil --info path/to/FlowDoro.app/Assets.car | grep -i icon
    ```
    Expect to see the icon name you supplied. If not, the catalog isn't being picked up by the target.

### Anti-patterns (Apple-explicit)

- ❌ Horizontal page-based TabView (deprecated visual)
- ❌ Modal sheets stacked >1 deep
- ❌ Tables with >5 visible rows (use `ScrollView` + `LazyVStack` or paginate)
- ❌ Two-finger gestures, long-press menus expecting precision
- ❌ Bright filled shapes during AOD
- ❌ ClockKit-only complications (won't ship on new face setups; deprecated watchOS 9)
- ❌ Group borders on watch — the screen edge is the border
- ❌ Custom haptic loops outside `WKInterfaceDevice.play(_:)` types
- ❌ Bumping `WATCHOS_DEPLOYMENT_TARGET` to gain Liquid Glass instead of `#available` conditional
- ❌ Two `AppIcon.appiconset` directories with the same name — Xcode picks one unpredictably; rename one and override `ASSETCATALOG_COMPILER_APPICON_NAME`

### Verification matrix (should run before claiming done)

| Check | Command | Pass signal |
|------|---------|-------------|
| watchOS builds | `xcodebuild -scheme FlowDoro-watchOS build CODE_SIGNING_ALLOWED=NO` | `BUILD SUCCEEDED` |
| Widget builds | (deps from watchOS scheme) | no widget-specific errors in log |
| Icon compiled | `xcrun assetutil --info <app>/Assets.car \| grep -i icon` | watch icon name appears |
| All schemes | iterate iOS/macOS/iOSWidget/watchOS | each `BUILD SUCCEEDED` |
| Quality | `./quality-check.sh` | pass count unchanged or improved |

Real-device-only (mark ⚠️ in scorecard, never ✅):
- Liquid Glass rendering at runtime
- AOD visual under wrist-down
- Smart Stack relevance ranking on the actual stack
- Live Activity Smart Stack pickup
- Home-grid icon appearance

---

## Tab-vs-drill-in path discipline on watchOS

When designing watch IA, every action should have exactly one canonical path.

### Tab vs navigation push — when to choose which

- **Tab** when the destination is a peer top-level surface that users want to reach from any state. Vertical TabView pages on watchOS feel like rooms, not screens.
- **Navigation push** when the destination is a hierarchical sub-screen of one parent (e.g. editing a single object's properties).

### Redundancy elimination rule

If a feature is reachable as both a tab AND an in-screen drill-in, delete the drill-in. Two paths to the same place fragment muscle memory and violate Calm Precision path-discipline.

Symptom: user discovers feature A via Tab; later discovers same feature A via Push from inside Tab B; now wonders if they're different.

### Worked example — FlowDoro WatchModePicker consolidation (build 74)

Build 73 introduced a vertical TabView with a Customize tab listing all modes. The pre-existing `WatchModePicker` view (reachable from Timer idle via "Change mode" navigation push) became redundant. Consolidation steps:

1. Confirm the drill-in's behavior is fully covered by the tab. If not, port the missing behavior first.
2. Delete the drill-in's source file. Glob-based target sources (xcodegen `path: watchOS`) make this a one-step removal — no manifest edit needed.
3. Remove the entry-point link from the parent screen. Replace with a small text hint ("swipe up for modes") so the affordance stays discoverable without a competing nav path.
4. Grep for zero references in `*.swift`, `*.yml`, `*.pbxproj`.

### Tap directness on list items

A tap on a list item that has only one logical follow-action should auto-flow into that action. Don't force tap → tap-edit-button.

- Preset row: single tap = select + return to invoking surface (set tab selection state).
- Configurable row (e.g. "Custom"): single tap = push the editor directly. Set the selection state in `simultaneousGesture` so when the user finishes editing and pops, the upstream surface already reflects the choice.

The intermediate "select, then tap an Edit button to configure" flow is two-tap UX where one will do.

## Visual delta audit pattern

For native iOS/macOS modernization passes scoped from a prior audit, run a three-pass workflow before opening any file:

1. **Source-code extraction** is load-bearing. The audit must cite `file:line` for every delta with a concrete suggested replacement. Vague reports ("inconsistent typography") force re-extraction during execute and double the cost. The audit doc becomes the spec.
2. **Canonical reference identification**. Name 2–3 modern reference views in the same repo (e.g. `AlertSettingsView`, `SettingsView`, `ProfileSettingsView`) so the audit's "good" target is in-tree, not abstract HIG.
3. **Structured delta report**. Group by category (color hardcodes / Dynamic Type / spacing / card pattern / interaction / chart hex), count, and rank by user-impact severity.

### IBR's role for native iOS

IBR's `native_scan` extracts a11y tree + bounds + screenshot. It does NOT extract computed font/color/spacing values — those live in the SwiftUI source. Use IBR for touch-target and a11y-label coverage; use grep + Read for the metric audit. Skip IBR snapshot capture in audit-only loops; reserve it for visual regression after a redesign that changes layout.

### Three-tier ROI ordering

Sequence fixes by descending impact:

1. **Colors + dark-mode safety** (highest ROI). Replacing `.foregroundColor(.white.opacity(N))` with `.foregroundStyle(.primary/.secondary/.tertiary)` simultaneously fixes dark-mode adaptation, WCAG contrast, and future-proofs against light-mode variants. Small line count, broad effect.
2. **Typography Dynamic Type**. Replacing `.font(.system(size: N))` with `.font(.caption / .footnote / .subheadline / .body / .headline / .title3)` enables accessibility text-size scaling. Keep hero anchors (28pt+ ultraLight KPIs) fixed and add `// Hero anchor: intentional fixed size` comments so future audits don't re-flag them.
3. **Tokens + spacing rhythm**. Hex literals → named Theme tokens; off-grid `padding(.vertical, 6/10/14)` → `Spacing.sm/md/lg`. Lowest individual impact, cumulative polish.

### WCAG body-text math for `.white.opacity(N)`

Against the LiquidGradientBackground core `#3A4878` (focus mode steel-blue) — a representative dark gradient core — alpha-composited approximations:

| Foreground | Approx contrast vs `#3A4878` | WCAG 4.5:1 (body) |
|---|---|---|
| `.white.opacity(0.9)` | ~10.6:1 | ✅ pass |
| `.white.opacity(0.7)` | ~5.7:1  | ✅ pass |
| `.white.opacity(0.5)` | ~3.8:1  | ⚠️ fails |
| `.white.opacity(0.35)` | ~2.4:1 | ❌ fails |
| `.white.opacity(0.3)` | ~2.1:1  | ❌ fails |

Anything at or below 0.5 on a dark gradient fails AA body. `.foregroundStyle(.secondary)` resolves to a SwiftUI-managed semantic color that is guaranteed ≥4.5:1 against the backing material in both light and dark color schemes — switch to it instead of tuning opacity by eye.

---

## WatchConnectivity callback wiring — avoiding dead-signal bugs

**Pattern.** When watch sends a signal via `WCSession.sendMessage(_:)`, iPhone's delegate `WCSessionDelegate.session(_:didReceiveMessage:)` decodes the payload and exposes a closure-based published callback on the connectivity manager:

```swift
var onBiometricBreakSignalReceived: ((BiometricBreakSignal) -> Void)?
```

A closure variable like this is useless unless something on the iPhone side assigns into it during app launch. The compiler will not catch a missing assignment because optional closures default to `nil` and silently no-op.

**Audit checkpoint.** For every published callback variable on a connectivity manager, grep for at least one assignment site somewhere in iOS code:

```bash
grep -rn "onMyCallback = " iOS/ Shared/
```

If grep returns zero hits, the entire feature path is dead — watch detection runs, the message arrives, and nothing happens on iPhone.

**FlowDoro example.** `onBiometricBreakSignalReceived` was declared in build 73 but inspection during build 78 found two related symptoms:
1. Build 73 wired the closure to `TimerEngine.handleBiometricBreakSignal`, which surfaces an in-app `CheckInData` sheet — but only when the iPhone app is foreground AND a flow session is running ≥15 minutes. In every other state (app backgrounded, no active session, app closed), the signal was effectively dropped.
2. Build 78 added a second path on the same closure: an opt-in time-sensitive `UNUserNotification` so the signal produces user-visible behavior even when the iPhone app is not in front. Default off; toggle in `AlertSettingsView`.

**Recommended patterns.**
- Prefer `NotificationCenter` for fan-out when more than one subscriber may need the signal — avoids closure-stomping where a later assignment overwrites an earlier one.
- Or use Combine `PassthroughSubject<Signal, Never>` for typed reactive flow with multi-subscriber semantics.
- Or make callback assignment a constructor parameter (`init(onSignal: @escaping (Signal) -> Void)`) so the compiler enforces wiring at instantiation.
- For closure-variable APIs that intentionally allow only one subscriber, add a unit test that calls `connectivityManager.simulateMessage(...)` and asserts a side effect — this catches dead-signal regressions before TestFlight.

**Coexisting paths.** When extending an existing closure with a second path (e.g. notification + in-app sheet), put both under the same closure body and gate each independently. Don't reassign the closure — the previous path will be silently lost.


## Test target wiring on XcodeGen Apple projects

A test target that exists in `project.yml` is only invokable by `xcodebuild test` if it is also a member of a scheme's `test.targets` array. Membership in `targets:` alone makes the bundle compile-clean but unreachable from the test action.

**Symptom.** `xcodebuild test -scheme FlowDoro-iOS -only-testing:FlowDoro-UnitTests` returns:

```
Cannot test target "FlowDoro-UnitTests"... isn't a member of the specified test plan or scheme
```

**Fix.** In `project.yml`, the scheme's `test.targets` array must include the test bundle by name:

```yaml
schemes:
  FlowDoro-iOS:
    test:
      config: Debug
      targets:
        - FlowDoro-UnitTests
        - FlowDoro-UITests
```

Then regenerate (`xcodegen generate --spec project.yml`).

**Platform alignment is part of the wiring.** The test target's `platform:` must match the scheme's runnable destinations. A `bundle.unit-test` declared `platform: macOS` cannot be run from an `FlowDoro-iOS` scheme via `-destination 'platform=iOS Simulator'` — xcodebuild surfaces:

```
Cannot test target "FlowDoro-UnitTests" on "iPhone 17 Pro": ... does not support iphonesimulator
```

If the wiring goal is "iOS scheme runs the unit tests on iPhone simulator," the test target itself must be `platform: iOS` (and any source files it pulls from `Shared/` need to compile cleanly for iOS — which is usually free since the iOS app target already compiles them). When you switch a previously-macOS test target to iOS, audit every scheme that referenced it: the macOS scheme's `test.targets` will silently break unless updated to drop the now-iOS test target or replaced with a separate macOS-platform test bundle.

**Audit checkpoint.** Before TestFlight, run a quick grep over `project.yml` to catch test targets not wired anywhere:

```bash
# every test target name should appear in at least one scheme's test action
yq '.targets | to_entries | map(select(.value.type | test("bundle"))) | .[].key' project.yml
yq '.schemes | to_entries | map(.value.test.targets // []) | flatten' project.yml
```

If a target name appears in the first list but not the second, the test bundle exists but no scheme can run it.

**FlowDoro example (build 79).** `FlowDoro-UnitTests` had been a `bundle.unit-test` on `platform: macOS`, wired into the macOS scheme's `test.targets` only. Builds 71-78 shipped without exercising any of the new code paths in CI. Build 79 added five test files covering AlertConfig codable, pomodoro notification identifier generation, Local Network permission classification, keychain-cache stability, and biometric break-signal default-off wiring. The test target was migrated to `platform: iOS`, added to `FlowDoro-iOS.schemes.test.targets`, and removed from the macOS scheme (which can no longer host an iOS-platform bundle). Net: `xcodebuild test -scheme FlowDoro-iOS -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -only-testing:FlowDoro-UnitTests` runs 51 tests in ~7 seconds.

**When test isolation must round-trip through `UserDefaults.standard`.** A common pattern in app-level singletons: `init(defaults: UserDefaults = .standard)` reads from injected defaults, but property `didSet` writes target `UserDefaults.standard` unconditionally. Tests that assert "value persists across two store instances" cannot rely on injected defaults for the write path — they have to either snapshot/restore `.standard` in `setUp`/`tearDown`, or refactor production to plumb the same defaults through both read and write. For a test-only access change this is heavier than a simple `private → internal` flip; document the constraint in the test file rather than push a deeper production change.
