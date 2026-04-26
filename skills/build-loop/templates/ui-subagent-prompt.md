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
   **Zero must-fix findings on any file you changed.** Warnings tolerated, must-fix blocks return.
3. If the scanner doesn't exist (older build-loop install), you still must self-audit against the checklist above before returning.

## Output requirements

Your output to the orchestrator must include:

- Files created / modified
- Build result (✅ / ❌)
- Scanner result on changed files: must-fix count (must be 0), warn count
- Every `RULE BEATS MOCKUP:` decision (one line each)
- Any anti-pattern you intentionally left in place + why (must be explicitly justified, not silently shipped)

Returning without these sections forces the orchestrator to re-prompt you. Don't skip.
