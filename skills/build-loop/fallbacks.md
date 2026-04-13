# Capability Fallbacks

Inline guidance the orchestrator embeds in subagent prompts when a preferred plugin is absent. Subagents do not inherit parent Skill context — only text in the prompt survives the dispatch boundary. Copy the relevant section verbatim into the subagent prompt.

Each section is self-contained. Keep prose tight: the goal is "capture the concept" for new users without the full toolkit, not to replicate the plugin.

---

## web-ui — Web UI build / validation

Design principles (Calm Precision, condensed from global `CLAUDE.md`):

- **Grouping**: single border around related items; dividers between rows. Never individual borders on list items.
- **Hierarchy**: Title 14–16px bold → Description 12–14px → Metadata 11–12px muted.
- **Touch / size**: ≥24px desktop, ≥44px mobile tap targets. Button size reflects intent weight.
- **Contrast**: ≥4.5:1 for text against background. Use WebAIM checker if uncertain.
- **Spacing**: 8pt grid. Use 4/8/16/24/32/48/64 px increments.
- **Signal**: Status = text color only, no background badges. Color + weight for hierarchy, not boxes.
- **Content ≥ Chrome**: ≥70% content ratio on any page.
- **Disclosure (Hick)**: show less, reveal on demand. Advanced options behind expand/more.
- **Nav selected state**: text-gray-900, font-medium, 2px bottom border. Never background pills.
- **Integrity**: no fake/placeholder buttons. Backend exists before UI.

Validation without IBR:

1. Visit the URL in a browser, DevTools open.
2. Tab through interactive elements — every one must be reachable and visibly focused.
3. Verify every button/link has a handler (click, submit, or `href`).
4. Check console for errors and warnings.
5. Screenshot the final state; compare against goal.

---

## mobile-ui — Mobile UI build / validation

Mobile-specific additions:

- **iOS (HIG)**: 44pt tap targets; use SF Symbols; respect safe-area insets (`safeAreaInset`, `UIEdgeInsets`); Dynamic Type support; minimum font 11pt.
- **Android (Material)**: 48dp tap targets; respect system back; elevation + shadows per Material guidelines; support gesture nav + 3-button nav.
- **React Native**: use `SafeAreaView` on every screen; test on both notched and non-notched devices.
- **Expo**: `useSafeAreaInsets()` from `react-native-safe-area-context`.
- **Keyboard**: inputs scroll into view; dismiss on outside tap; `returnKeyType` matches action.
- **Offline**: assume it; show cached state with staleness indicator.
- **Performance**: defer images until in viewport; avoid re-renders during scroll.

Validation without IBR:

1. Run on smallest supported device (iPhone SE / small Android).
2. Run on largest (iPad Pro / tablet).
3. Toggle Dark Mode, Dynamic Type (iOS), font-scale (Android) — layout still works.
4. Put airplane mode on — app degrades gracefully.

---

## design-tokens — Design system tokens

**Do not hardcode the user's design system.** Tokens are project-specific.

Source-of-truth check order:

1. `.ibr/design-system.json` — IBR-managed tokens
2. `tailwind.config.{ts,js,mjs}` → `theme.extend` → colors/spacing/typography
3. `tokens.json` or `design-tokens.json` at project root
4. `src/styles/tokens.css` or `globals.css` — `:root { --color-*: ... }`
5. iOS: `Assets.xcassets/Colors/*.colorset` + `Assets.xcassets/*.appiconset`
6. Figma export file (if present) — `design/tokens.json`

If none exist: ask the user. Do not invent a palette.

When adding a new component: reuse existing tokens. Never introduce a new hex literal without confirming with the user.

---

## screenshot — Visual evidence

Preferred tools (in order of availability):

1. `showcase:capture` slash command
2. `ibr:screenshot`
3. `npx playwright screenshot <url> <output.png>` — if Playwright installed
4. macOS: `screencapture -i <output.png>` for desktop apps
5. Ask the user to attach a screenshot — never fabricate one

Save to `.build-loop/evals/screenshots/YYYY-MM-DD-<label>.png`. Reference paths in the scorecard.

---

## web-fetch — External content fetching

Inline minimal fetch for when `scraper-app` SDK is absent. Still cheaper than having the LLM read raw HTML:

```js
// Strip chrome, keep article content. Zero deps.
const html = await (await fetch(url)).text();
const body = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i)?.[1] ?? html;
const text = body
  .replace(/<script[\s\S]*?<\/script>/gi, "")
  .replace(/<style[\s\S]*?<\/style>/gi, "")
  .replace(/<[^>]+>/g, " ")
  .replace(/\s+/g, " ")
  .trim();
```

**Always note LLM cost** in the Phase 8 report when this fallback runs. Flag that installing `scraper-app` would eliminate the token spend.

Do not fetch URLs the user did not explicitly provide or that aren't core to the task. Never fetch from private networks.

---

## debug — Root-cause investigation

When `claude-code-debugger:debug-loop` is unavailable:

1. **Reproduce** — minimal case that fails every time. Write the command/steps down.
2. **Isolate** — binary-search the diff / commits / inputs until you find the smallest change that flips pass ↔ fail.
3. **Hypothesize** — one specific claim about cause. Write it as a statement, not a question.
4. **Test** — make the smallest possible change that would confirm or refute the hypothesis. Run it. Observe.
5. **Record** — append one line to `.build-loop/issues/YYYY-MM-DD-<slug>.md` with: symptom, root cause, fix, prevention.

Stop after 3 failed hypotheses and escalate to the user with what was tried.

---

## bug-memory — Prior-bug lookup

When `claude-code-debugger:debugging-memory` is unavailable, grep the consumer project:

```sh
grep -R -l "<error-substring>" .build-loop/issues/ 2>/dev/null
grep -R -l "<error-substring>" .bookmark/ 2>/dev/null
```

Read any matches before starting a fresh investigation. Do not dispatch a debug subagent without checking.

---

## agent-authoring — Writing new agents

Checklist when `agent-builder` / `plugin-dev:agent-development` is unavailable:

Required frontmatter:

```yaml
---
name: agent-slug                   # kebab-case, matches filename
description: |
  One-sentence trigger condition.
  <example>
  Context: ...
  user: "..."
  assistant: "I'll use the <agent-slug> agent to ..."
  </example>
model: inherit                     # or sonnet / haiku / opus
color: blue                        # used in the UI
tools: ["Read", "Grep", "Glob"]    # least-privilege; don't default to all
---
```

Body: second-person instructions (`You are ...`). Single focused responsibility. No "also handles …" scope creep. ≤150 lines.

Invocation from the orchestrator: pass complete context in the prompt — agents do not inherit parent Skill or file-read context.

---

## structured-writing — Reports, summaries, handoffs

When `pyramid-principle:*` is unavailable, use the SCQA-to-key-line format:

1. **Situation** — one sentence on the status quo.
2. **Complication** — one sentence on what changed or what's at stake.
3. **Question** — the implicit question the reader is now asking.
4. **Answer (the governing thought)** — one sentence, top of the document.
5. **Key lines (3–7)** — MECE arguments that support the governing thought. Each is a claim, not a topic.
6. **Support** — evidence under each key line.

For Phase 8 scorecard: governing thought = did the build meet the goal; key lines = the scoring criteria; support = evidence rows.

---

## migration — Hosted-IDE → production migration

When `replit-migrate:*` is unavailable. Applies to Replit, Lovable, Bolt.new, v0, CodeSandbox, StackBlitz exports.

Inventory pass — what does the source have?

- Routes and endpoints (file + verb + params + response shape)
- Auth mechanism (session cookie? JWT? OAuth? custom?)
- Database (SQLite? Postgres? JSON files? LocalStorage?)
- File storage (local? S3-like? CDN?)
- Environment variables (list them all with intended values)
- External API calls (with keys — flag any that need rotation)
- Assets (images, fonts, icons — source and license)
- Build scripts (package.json scripts, Makefile, replit.nix)
- Hosted-IDE lock-in (platform-specific APIs, proprietary secrets store, always-on URLs)

Translation guide — pick stacks that fit the target:

| Source | Web target | Native target |
|---|---|---|
| Express / Koa | Next.js API routes or Hono | N/A |
| Prisma | Drizzle (Vercel/Cloudflare) or Prisma | SwiftData |
| Replit DB / Redis | Upstash Redis / Neon / Turso | SwiftData |
| Replit Auth | Better Auth / NextAuth / Clerk | Sign in with Apple |
| LocalStorage | cookies or DB | UserDefaults |
| `.replit` runner | `vercel.json` / `wrangler.toml` | Xcode scheme |

Parity verification — every route/feature needs a smoke test against the migrated version before cut-over. Write these tests first.

---

## prompt — Prompt authoring / review / audit

When the `prompt-builder:prompt-builder` plugin skill is unavailable. If the personal `prompt-builder` skill is available (same name, loaded via Skill tool), load it first. It covers technique selection (CoT, SoT, few-shot, self-consistency) in more depth than this fallback.

Use the **6-Part Stack** for any system prompt or agent prompt:

1. **Role** — who the model is. One sentence, specific. "You are a triage agent for customer support tickets that classifies urgency."
2. **Task** — the specific action. Verbs. No hedging. "Classify each ticket as P0/P1/P2."
3. **Constraints** — hard limits: length, forbidden behaviors, tools it can/cannot use, response time, data it must not output.
4. **Context** — what the model needs to know: schema of inputs, definitions of ambiguous terms, org-specific conventions.
5. **Output format** — exact structure: JSON schema, markdown template, or free text with labeled sections. Specify escape behavior for ambiguous inputs.
6. **Acceptance criteria** — how success is judged. If deterministic, what makes it wrong. If LLM-judged, what the judge looks for.

Calibrate to model tier:

- **Frontier (T1 — Opus 4.6, GPT-5)**: can handle longer instructions, implicit reasoning, self-correction. Prefer clarity over verbosity.
- **Mid (T2 — Sonnet 4.6, GPT-4)**: explicit instructions; show, don't tell; 1-2 few-shot examples help.
- **Small/fast (T3 — Haiku 4.5, gpt-4-mini)**: keep prompts short; single task only; deterministic output format; more examples (3-5).

Review checklist — when auditing an existing prompt:

1. Does it leak system implementation details the user shouldn't see?
2. Are there [ASSUMED] values (thresholds, formats, user intent) that should be surfaced as parameters?
3. Are there contradictions between constraints and examples?
4. Would two reasonable readers interpret the task the same way?
5. Is the output format machine-parseable if it's downstream of code?
6. What happens on edge inputs — empty string, very long string, non-English, adversarial?
7. Is the role specific enough to constrain behavior, or vague enough to be ignored?

Temperature hints:

- 0.0-0.2 — classification, extraction, deterministic tasks
- 0.3-0.5 — structured generation (summaries, rewrites)
- 0.7-1.0 — open-ended creative work
- Rarely above 1.0 — only for diversification across multiple samples

Save iterated prompts to `.build-loop/prompts/` with a version suffix so regressions are detectable.

---

## apple-dev — iOS / watchOS / macOS

When the personal `apple-dev` skill is unavailable (new user, no `~/.claude/skills/apple-dev/`):

Minimal SwiftUI scaffold:

```swift
import SwiftUI
import SwiftData

@main
struct App: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .modelContainer(for: [Item.self])
    }
}

@Model
final class Item {
    var timestamp: Date
    init(timestamp: Date = .now) { self.timestamp = timestamp }
}
```

Build via XcodeGen (`project.yml`) rather than hand-editing `.pbxproj`:

```yaml
name: MyApp
options:
  bundleIdPrefix: com.example
targets:
  MyApp:
    type: application
    platform: iOS
    deploymentTarget: "17.0"
    sources: [MyApp]
    settings:
      base:
        DEVELOPMENT_TEAM: ABCDE12345
```

Deployment to TestFlight — App Store Connect API key:

```sh
xcrun altool --upload-app \
  -f build/MyApp.ipa \
  -t ios \
  --apiKey $ASC_KEY_ID \
  --apiIssuer $ASC_ISSUER_ID
```

Notes:

- Use API key auth (`--apiKey`), not username/password. Keys in `~/.appstoreconnect/private_keys/AuthKey_<ID>.p8`.
- 44pt tap targets (HIG). Dynamic Type at every font size. VoiceOver labels on every tappable view.
- Watch connectivity: use `WCSession` with `transferUserInfo` for background sync, `sendMessage` only when reachable.
- Do not copy Apple Developer Program credentials into the repo. Read from Keychain or environment.
