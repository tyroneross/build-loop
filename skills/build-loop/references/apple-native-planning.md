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
