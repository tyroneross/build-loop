# UI Subagent Prompt — Design Rule Preamble

> Prepended verbatim to every build-loop subagent prompt that touches UI files. Do NOT replace with a link — agents miss links.

---

## Required reading BEFORE you touch any UI file

Load these skills and follow them. Not optional.

- **All UI work**: `calm-precision`
- **SwiftUI / Apple platforms**: also load `ibr:ios-design` and `ibr:apple-platform`
- **React / Vue / web**: also load `frontend-design` and `ibr:mobile-web-ui`

If a skill is unavailable, proceed but note it in your output.

## Mockup-vs-rule conflict policy

Mockups are **intent**. Design rules are **law**. When they conflict, the rule wins. No exceptions, no judgment calls — replicate the rule, not the pixel.

Required behavior on conflict:
1. Implement per the rule.
2. Add a line to your output: `RULE BEATS MOCKUP: <rule-id> overrides <mockup-element>. Reason: <one sentence>.`

Skipping this line means the orchestrator can't audit your decisions. Don't skip.

## Anti-pattern checklist (zero tolerance)

Every UI file you write or modify must pass this checklist before you return.

1. **No status pills.** Status indicators use text color only. Never `.background(opacity).clipShape(Capsule())` (SwiftUI) or `bg-X-500 rounded-full` (Tailwind) near a chip / badge / status / trend / score identifier. Color and weight create hierarchy, not boxes.

2. **Reduce Motion gates every animation.** No `.repeatForever`, `withAnimation`, `.easeInOut(duration:)`, `animate-pulse`, `animate-spin` without the platform-correct guard.
   - SwiftUI: `@Environment(\.accessibilityReduceMotion) private var reduceMotion`, then `if !reduceMotion { withAnimation(...) }`
   - Web: pair every animation utility with its `motion-reduce:` variant or media-query equivalent

3. **Theme tokens, not literals.**
   - SwiftUI: no raw `UIColor(red:green:blue:)` outside `Theme/`. No literal `.cornerRadius(N)` — use `Theme.cornerSmall/Medium/Large/XL`.
   - Web: no `#hex` outside theme/tokens files. No `text-[Npx]` arbitrary sizes.

4. **Dynamic Type / responsive type.** Body copy uses theme tokens (`Theme.fontBody`, `Theme.fontCaption`), not `.font(.system(size: N))`. Numeric chips and tabular layouts may use fixed sizes.

5. **Every icon has a label.** Icon-only `Image(systemName: "...")` (SwiftUI) or `<Icon />` (web) gets an explicit `accessibilityLabel` / `aria-label`, OR is wrapped in a `Button` whose label covers it.

6. **Touch targets.** Mobile: 44pt minimum. Primary thumb-zone actions (record, submit during capture): 88pt minimum.

7. **VoiceOver matches visible text.** Don't strip signs (`abs()`) for one but not the other. Keep numeric conventions consistent across visible label and accessibility label.

8. **No fake buttons.** Every interactive element has a working backend handler. No placeholder onTap, no "Coming soon" without the affordance being hidden or marked.

9. **Floating tab bar / system overlays don't clip your content.** iOS 26's floating tab bar overlays the bottom ~92pt of the window. Android navigation gesture inset is similar. Web bottom-fixed nav same idea. **Every scrollable surface needs an explicit bottom inset** so the last row clears the bar at scroll-end:
   - SwiftUI ScrollView: append `Color.clear.frame(height: 100)` as the last child of the inner VStack, OR use `.contentMargins(.bottom, 100, for: .scrollContent)`
   - SwiftUI List: `.safeAreaInset(edge: .bottom) { Color.clear.frame(height: 100) }`
   - Web: equivalent `pb-24` / `padding-block-end: 6rem` on the scrollable container
   `safeAreaPadding` alone is not sufficient — it pushes the ScrollView frame, not the scrollable content area. This shipped broken in a real build (build 53) because the rule wasn't on the checklist.

10. **Numeric/text fields constrain width.** Long dim names ("Engagement", "Conciseness") wrap or squeeze adjacent columns when no width policy is set. For row layouts where label + score columns coexist:
    - Labels: `.lineLimit(1).minimumScaleFactor(0.75)` — shrink, don't wrap
    - Right-side numeric column: `.fixedSize(horizontal: true, vertical: false)` — never sacrifice the timestamp/score for label width

11. **Custom-drawn arcs / charts: render the half you mean.** SwiftUI's `Path.addArc` `clockwise:` flag is counter-intuitive in the user-space (y-flipped) coordinate system. `clockwise: false` from 180° to 360° draws the TOP half. `clockwise: true` draws the BOTTOM half. Use `Circle().trim(from:to:).rotationEffect(_:)` for predictable results. Track strokes against dark backgrounds need at least `Theme.textMuted.opacity(0.25)` contrast — not `surfacePrimary` (which is too close to background luminance). This shipped a literal upside-down semicircle (build 53/55) before being caught visually.

## Required SwiftUI environment hooks

When your file contains any animation:

```swift
@Environment(\.accessibilityReduceMotion) private var reduceMotion

// Then everywhere you animate:
if !reduceMotion {
    withAnimation(...) { ... }
}
```

## Verification before you return

Mandatory pre-return steps:

1. **Build.** Platform default — SwiftUI: `xcodegen generate && xcodebuild build`. Web: `pnpm build` or project equivalent.

2. **Design-rule scan.** If `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/scanners/audit-design-rules.mjs` exists, run:
   ```
   node "${CLAUDE_PLUGIN_ROOT}/skills/build-loop/scanners/audit-design-rules.mjs" --root=<project> --platform=<swiftui|react|web>
   ```
   **Zero must-fix findings.** Must-fix blocks return.

   **No "out of scope" framing for warnings.** Drift from a rule in any user-facing file is drift, regardless of whether this commit "owns" that file. If your task changes one screen and the scanner shows warnings on adjacent screens, fix the warnings too — the user lives in the app, they don't see commit boundaries. The only acceptable warning is one you explicitly justify in your output (e.g. "fixed font size on numeric chip — Dynamic Type would break tabular alignment"). "Pre-existing" is not a reason; it's a backlog.

3. **Visual validation (REQUIRED for UI work).** The scanner catches static anti-patterns. It cannot catch rendering bugs — an upside-down arc, an invisible track, a clipped row, a chip that wraps. **If you touched a Views/ file, you must render the actual screen and look at it.**

   - **iOS / macOS / watchOS**: install on simulator, launch, capture via `mcp__plugin_ibr_ibr__native_scan` or `xcrun simctl io booted screenshot`. If your change is in a returning-user code path, seed test data first (see "DebugSeeder pattern" below).
   - **Web**: open in headless Chromium via `mcp__plugin_ibr_ibr__scan` against the dev server URL.

   Compare against the mockup if one exists. Look for: arc/chart geometry rendering correctly, score values positioned where the mockup shows them, no rows clipped behind floating bars / tab bars / safe areas, no text wrapping unexpectedly, no overlapping elements. Real shipped bugs (build 53–55: gauge upside-down with stray ticks; multiple tabs clipping last row behind iOS 26 floating bar) were only caught by visual inspection.

   If IBR / simulator unavailable, document this in your output as a known gap rather than skipping the step silently.

4. If the scanner doesn't exist (older build-loop install), you still must self-audit against the checklist above before returning.

## DebugSeeder pattern (testability for returning-user views)

Many UI states are only reachable after the user has done something — completed N drills, set a name, joined a streak. Manually doing 7 drills before you can verify a Home returning-user view is not a sustainable verification path. **Add a debug-only seeder so any subagent (or a human reviewer) can verify these states in seconds.**

Pattern (SwiftUI/SwiftData example):

```swift
// SpeakSavvy/Services/DebugSeeder.swift
#if DEBUG
import Foundation
import SwiftData

enum DebugSeeder {
    static func seedIfEmpty(context: ModelContext) {
        let existing = (try? context.fetch(FetchDescriptor<Session>())) ?? []
        guard existing.isEmpty else { return }
        // ... insert representative test data spanning a meaningful range
        try? context.save()
    }
}
#endif
```

```swift
// SpeakSavvyApp.swift init()
#if DEBUG
if CommandLine.arguments.contains("-SeedDebugSessions") {
    DebugSeeder.seedIfEmpty(context: container.mainContext)
}
#endif
```

Then launch:
```
xcrun simctl launch booted <bundle-id> -SeedDebugSessions YES
```

For multi-tab apps, also add a `-SelectedTab N` launch arg so any tab can be opened directly without taps. Both are gated by `#if DEBUG` and compile out of release builds — zero production cost.

**Use the seeder.** Subagents that report "I trust the math, ship it" without rendering the actual screen will miss visual bugs. Build 55 shipped a broken gauge because no one rendered it. Build 56 caught it because the seeder existed. The seeder pays for itself the first time it catches a bug.

## Output requirements

Your output to the orchestrator must include:

- Files created / modified
- Build result (✅ / ❌)
- Scanner result on changed files: must-fix count (must be 0), warn count
- Every `RULE BEATS MOCKUP:` decision (one line each)
- Any anti-pattern you intentionally left in place + why (must be explicitly justified, not silently shipped)

Returning without these sections forces the orchestrator to re-prompt you. Don't skip.
