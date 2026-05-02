# iOS Notification / Alarm Diagnostic Playbook

Domain reference for diagnosing "the alarm doesn't fire" on iOS apps that use `UNUserNotificationCenter` for timed completion alerts (Pomodoro timers, meditation apps, fasting trackers, sleep alarms, anything with a scheduled future notification).

Source: build-loop run on FlowDoro (2026-04-26). Single high-confidence root cause; the playbook generalizes the diagnostic moves.

## Smoking-gun pattern

> Notification scheduled ONLY at background transition + unconditionally cancelled on every foreground return.

If the codebase has both of these, several normal user flows leave nothing armed. Before doing anything else, search for these two patterns; if both exist, you have your root cause in under 60 seconds.

```bash
grep -n "scheduleTimedNotification\|scheduleCompletion" --include="*.swift" -r .
grep -n "removeAllPendingNotificationRequests\|cancelScheduled" --include="*.swift" -r .
```

If schedule sites are inside `applicationDidEnterBackground`, `scenePhase == .background`, or a single `handleBackgroundTransition`, **and** cancel sites run on every foreground return without checking timer state, file the finding as `KNOWN_FIX` against `INC_IOS_ALARM_SCHEDULE_TIMING`.

## Hypothesis tree (8 priorities)

When the smoking-gun pattern doesn't apply, descend in this order. Stop at the first confirmed cause.

1. **Schedule timing** — Where is `UNUserNotificationCenterAdd` actually called? If the only call site is a background-transition hook, force-quit / fast suspend / foreground-only sessions never arm.
2. **Foreground cancel** — Is `removePendingNotificationRequests` called unconditionally on foreground? After fixing #1, this is the new bug.
3. **Authorization timing** — When is `requestAuthorization` invoked? If "after first completion," the first session runs unauthorized and silently fails to deliver.
4. **Identifier collisions** — Are multiple call sites adding requests with different UUIDs? Cancellation by-identifier won't catch them; the OS may dedupe or the app may leak pending requests.
5. **Trigger interval** — Is `timeInterval` ever 0 or negative (clock skew, timer overshoot)? `UNTimeIntervalNotificationTrigger` requires `> 0`.
6. **Sound asset path** — Is the chime asset actually copied into `Bundle.main`? `UNNotificationSound(named:)` silently falls back to no sound if the file isn't found.
7. **Delegate registration** — Is `UNUserNotificationCenter.current().delegate` set, and does `willPresent` return `[.banner, .sound]`? If not, foreground delivery is invisible.
8. **Interruption level + Focus** — Is `interruptionLevel = .timeSensitive` set, and is the iOS Time Sensitive entitlement active? Without it, Focus modes suppress the banner.

## Parallel-investigator decomposition

For "alarm doesn't fire," dispatch 3 concurrent investigators. None share state.

| Investigator | Scope | Files / Symbols |
|---|---|---|
| **Timer architecture** | Where is the notification scheduled, where is it cancelled, what other paths affect lifetime | `*TimerEngine*.swift`, `*Notification*.swift`, every `scheduleCompletion` / `cancelScheduled*` call site |
| **Audio + haptic routing** | Foreground completion path, AVAudioSession config, ringer-bypass, CoreHaptics fallbacks | `*AudioService*.swift`, `AudioSessionManager*`, `playChime`, `UINotificationFeedbackGenerator`, `CHHapticEngine` |
| **Lifecycle + cancel** | scenePhase transitions, app delegate hooks, force-quit handling, ActiveSessionRecovery | `FlowDoroApp+iOS.swift` or `App+iOS.swift`, `applicationWillTerminate`, `scenePhase.onChange`, recovery code |

## Symbol search lists

Run these in any iOS project as a starting cluster.

```bash
# Notification lifecycle
grep -rn "UNUserNotificationCenter\|UNTimeIntervalNotificationTrigger\|UNNotificationRequest" --include="*.swift" .
grep -rn "removePendingNotificationRequests\|removeAllPendingNotificationRequests" --include="*.swift" .

# Authorization
grep -rn "requestAuthorization\|getNotificationSettings\|UNAuthorizationStatus" --include="*.swift" .

# Foreground completion path
grep -rn "willPresent\|UNNotificationPresentationOptions\|interruptionLevel" --include="*.swift" .

# Audio routing
grep -rn "AVAudioSession\|AVAudioPlayer\|AudioServicesPlaySystemSound" --include="*.swift" .

# Haptics
grep -rn "CHHapticEngine\|UINotificationFeedbackGenerator\|UIImpactFeedbackGenerator" --include="*.swift" .

# Lifecycle
grep -rn "scenePhase\|applicationDidEnterBackground\|applicationWillTerminate" --include="*.swift" .
```

## File anti-patterns to flag

Treat the presence of any of these as a high-priority finding.

| Anti-pattern | Where it bites |
|---|---|
| `removeAllPendingNotificationRequests()` called outside reset/teardown | Wipes unrelated app notifications, masks identifier mistakes, blocks per-session debugging |
| Schedule call inside `if scenePhase == .background` only | Force-quit, fast suspend, foreground-only sessions go unarmed |
| Foreground transition unconditionally calls `cancelScheduledNotifications()` | Lock-then-quick-unlock cancels the alarm while the timer is still running |
| `requestAuthorization` invoked after first session completion | First session is silent; user-perceived "alarm broken" |
| `AVAudioPlayer` chime with no fallback to `.default` | Missing or corrupted bundled asset = silent foreground completion |
| `UNNotificationSound(named: "chime.caf")` without `Bundle.main.url(forResource:withExtension:)` check | Silent fail at delivery time, no log |
| Single delegate impl on a multi-platform target without `#if os(...)` for `badgeSetting`, `UIImpactFeedbackGenerator`, etc. | watchOS / macOS build breaks; or worse, runtime crashes |
| `UIBackgroundModes: [audio]` to keep the timer "alive" | App Store rejection; not the fix |

## Verification matrix (15-state iPhone)

Tier the matrix by what each state actually exercises. **Sim-verifiable** runs in `xcrun simctl`; **real-device** needs hardware.

| # | Scenario | Expected | Sim | Device |
|---|---|---|---|---|
| 1 | Foreground tick to zero | banner + sound + haptic | sound | haptic |
| 2 | Background then zero | banner + sound while locked | ✅ | ✅ |
| 3 | Locked then zero | lock-screen banner + sound | ✅ | ✅ |
| 4 | Force-quit then zero | banner still fires | ✅ | ✅ |
| 5 | Pause → resume → zero | fires at new endDate | ✅ | ✅ |
| 6 | Reset mid-session | no banner | ✅ | ✅ |
| 7 | Scrub: bg → fg → bg → zero | banner fires once | ✅ | ✅ |
| 8 | Focus mode allowed | banner fires | ✅ | ✅ |
| 9 | Focus mode blocked | banner suppressed (expected) | ✅ | ✅ |
| 10 | Silent switch on | sound through (AVAudioSession.playback) | n/a | device-only |
| 11 | DND on | banner suppressed unless Critical Alerts | n/a | device-only |
| 12 | Permissions: notDetermined | first Start triggers prompt | ✅ | ✅ |
| 13 | Permissions: denied | no banner, no crash, log warns | ✅ | ✅ |
| 14 | Backgrounded > 90 min | recovery flow | ✅ | ✅ |
| 15 | Watch session running concurrently | iPhone alarm independent | partial | ✅ |

## Diagnostic logging template

Every iOS notification fix should ship with structured logging on day 1, not as a follow-up. Recommended subsystem and category:

```swift
import os.log

enum AlertDiagnostics {
    private static let logger = Logger(
        subsystem: Bundle.main.bundleIdentifier ?? "com.example",
        category: "alert-diag"
    )
    static func scheduled(identifier: String, afterSeconds: Int, source: String) { /* ... */ }
    static func cancelled(identifier: String, source: String) { /* ... */ }
    static func cancelSkipped(reason: String, source: String) { /* ... */ }
    static func scenePhase(_ phase: String) { /* ... */ }
    static func dumpAuthSettings() { /* ... */ }
    static func dumpPendingRequests(reason: String) { /* ... */ }
}
```

Filter Console.app on `subsystem:com.example category:alert-diag` to read the trail. The `[alert-diag]` prefix and `id=...` tokens make grep one-shot.

## Verdict templates

When this playbook applies, use one of the following incident IDs.

- `INC_IOS_ALARM_SCHEDULE_TIMING` — schedule call site only at background transition
- `INC_IOS_ALARM_UNCONDITIONAL_CANCEL` — foreground cancel runs while timer still active
- `INC_IOS_ALARM_AUTH_LATE` — requestAuthorization after first completion
- `INC_IOS_ALARM_CHIME_MISSING` — bundled audio asset not in Bundle.main
- `INC_IOS_ALARM_FOCUS_BLOCKED` — interruption level missing or Focus suppressing

## Confidence

This playbook is built from one canonical incident (FlowDoro 2026-04-26). Promote to "pattern" status (`PTN_IOS_ALARM_*`) once 3+ incidents share the smoking-gun signature.

---

## Multi-alert identifier-set pattern

Added 2026-04-26 (FlowDoro build 72) when alert customization landed: count, persistence (re-fire every N seconds), and backup escalation expanded the per-transition notification stack from 1 request to up to ~10. The schedule and cancel paths must agree on the identifier set or the cancel will silently leak requests.

### The bug pattern this prevents

> User changes alert count from 3 to 1 mid-session. Pause cancels with the legacy single-identifier `[timerCompletionIdentifier]`. The other two requests survive. Pomodoro fires three notifications during a paused session — exactly the failure mode persistence+backup were trying to avoid.

The same shape appears whenever a feature changes the cardinality or naming of pending requests (persistence toggles, A/B sound tests, multi-stage celebrations). The cancel path is written first against a smaller cardinality, then someone bumps the cardinality on the schedule side without updating cancel.

### The rule

**Schedule and cancel must call a single identifier-set generator function, parameterized by the same inputs.** Any feature that adds requests to a session bumps the cardinality through that one function, and both sides update for free.

```swift
static func pomodoroNotificationIdentifiers(sessionTag: String, config: AlertConfig) -> [String] {
    var ids: [String] = [timerCompletionIdentifier] // legacy single id, always cleared
    let prefix = "\(timerCompletionIdentifier).tag.\(sessionTag)"
    for i in 0..<max(1, min(3, config.count)) {
        ids.append("\(prefix).alert.\(i)")
    }
    if config.persistenceEnabled {
        let n = min(6, max(0, config.persistenceMaxSec / max(15, config.persistenceIntervalSec)))
        for j in 0..<n { ids.append("\(prefix).persist.\(j)") }
    }
    return ids
}
```

`scheduleCompletion(...)` calls this and adds requests under each id. `cancelScheduledCompletion(config:sessionTag:source:)` calls the same function and passes the array to `removePendingNotificationRequests(withIdentifiers:)`. The legacy bare identifier stays in the set forever so a clean upgrade absorbs any in-flight build-71 request.

### Caller responsibility

The caller (TimerEngine) remembers `armedSessionTag` and `armedConfig` at schedule time and passes them back at cancel time:

```swift
private func armCompletionStack(...) {
    let config = alertSettings.config(forBreak: forBreak)
    let tag = currentSessionTag(forBreak: forBreak)
    armedSessionTag = tag
    armedConfig = config
    delegate?.scheduleTimedNotificationWithConfig(..., config: config, sessionTag: tag, ...)
}

private func cancelCompletionStack(source: String) {
    if let tag = armedSessionTag, let config = armedConfig {
        delegate?.cancelScheduledNotificationsWithConfig(config: config, sessionTag: tag, source: source)
    } else {
        delegate?.cancelScheduledNotifications(source: source) // legacy fallback
    }
    armedSessionTag = nil
    armedConfig = nil
}
```

`sessionTag` is derived from `phaseStartDate` so re-arming during a phase keeps the same tag (idempotent replace), but a new phase gets a fresh tag (so the prior phase's stack is not accidentally cancelled by a subsequent cancel call).

### Diagnostics

Log the full identifier list, not the count alone, on every schedule and cancel. `[alert-diag] schedule-stack ids=[a,b,c,...] cfg-count=3 persist=true` is greppable; "scheduled 3 notifications" is not.

### Smoking gun for the bug

`getPendingNotificationRequests` returns N entries after a "cancel everything" lifecycle hook (pause, reset, foreground-not-running). The fix is always: route the cancel through the same generator the schedule used.

---

## iOS Local Network discovery checklist

Added 2026-04-26 (FlowDoro build 72) when "Listener failed: NWError -65569 DefunctConnection" appeared in Profile → Nearby Devices. iOS Local Network has three independent failure modes; check them in this order.

### 1. Plist stack (one-time setup, easy to forget)

```xml
<!-- Info.plist for iOS, INFOPLIST_KEY_* in xcconfig/project.yml for macOS -->
<key>NSLocalNetworkUsageDescription</key>
<string>Why your app needs the local network — user-readable, shown in the system permission prompt.</string>
<key>NSBonjourServices</key>
<array>
  <string>_yourapp._tcp</string>
</array>
```

If `NSLocalNetworkUsageDescription` is missing, iOS 14+ kills the listener on creation and the user never sees a permission prompt — so they cannot grant access. Symptom is identical to "permission denied". The service type in `NSBonjourServices` MUST match the string passed to `NWBrowser` and `NWListener.Service(type:)` exactly. A typo silently produces empty browser results.

### 2. NWListener / NWBrowser retain rule

Listener and browser must be retained as instance properties:

```swift
final class LocalNetworkSync {
    private var listener: NWListener?  // strong reference required
    private var browser: NWBrowser?
}
```

A local `let listener = try NWListener(...)` inside a function is destroyed at scope exit. The mDNSResponder XPC connection drops with it, the user sees nothing, and the bug presents as "no devices found" with no error path.

### 3. Distinguish -65569 (transient) from -65570 (permission)

| Code   | NWError shape         | Meaning                             | Action                          |
|--------|-----------------------|-------------------------------------|---------------------------------|
| -65569 | `.dns(-65569)`        | DefunctConnection — mDNSResponder XPC dropped (wifi change, simulator quirk, suspension) | Restart with bounded backoff |
| -65570 | `.dns(-65570)`        | Permission denied / not granted     | Show "Open Settings" — DO NOT restart |

The historical fix of "show NWError.localizedDescription" surfaces both as raw jargon. Map them:

```swift
nonisolated private static func isPermissionDenied(_ error: NWError) -> Bool {
    if case .dns(let code) = error, Int(code) == -65570 { return true }
    return false
}
```

UI translation: -65569 should say "Searching… reconnecting after a network change." (recoverable). -65570 should show a permission card with an Open Settings button (`UIApplication.openSettingsURLString`).

### 4. Restart-on-defunct backoff

`.failed(-65569)` on a retained listener requires recreation — the existing `NWListener` instance is permanently dead. Bounded backoff prevents battery drain on a permanently broken environment:

```swift
private static let restartDelaysSec: [Double] = [1.0, 3.0, 8.0]
private var listenerRestartAttempts: Int = 0

private func scheduleListenerRestart() {
    let delay = Self.restartDelaysSec[min(listenerRestartAttempts, Self.restartDelaysSec.count - 1)]
    listenerRestartAttempts += 1
    Task { @MainActor [weak self] in
        try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        guard let self, self.isActive, !self.permissionDenied else { return }
        self.listener?.cancel()
        self.listener = nil
        self.startListener()
    }
}
```

Reset `listenerRestartAttempts = 0` on `.ready` so a successful restart re-enables fast retry on the next failure. **Suspend restart while `permissionDenied` is true** — there is no point restarting a listener the system is going to kill again until the user grants permission.

### 5. Smoking gun signatures

| Symptom                                                                | Cause                                |
|------------------------------------------------------------------------|--------------------------------------|
| Listener fails on first launch, no permission prompt ever shown        | Missing `NSLocalNetworkUsageDescription` |
| Browser results always empty despite peers running                     | Service type mismatch between Plist and code |
| Listener works in foreground, dies after backgrounding                 | Listener stored in a local, not retained |
| -65569 appears intermittently after wifi changes / app resume          | Normal — needs restart-on-defunct backoff |
| -65570 with no in-Settings entry for the app                           | Plist string was added after first launch — uninstall/reinstall to retrigger |

### Incident IDs

- `INC_IOS_LOCAL_NETWORK_DEFUNCT` — -65569 with no restart logic
- `INC_IOS_LOCAL_NETWORK_PLIST` — missing usage description or Bonjour service entry
- `INC_IOS_LOCAL_NETWORK_RETAIN` — listener/browser destroyed at scope exit
- `INC_IOS_LOCAL_NETWORK_PERMISSION_UI` — raw NWError shown to user instead of actionable Open Settings prompt
