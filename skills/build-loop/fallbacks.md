# Capability Fallbacks

Inline guidance the orchestrator embeds in subagent prompts when a preferred plugin is absent. Subagents do not inherit parent Skill context — only text in the prompt survives the dispatch boundary. Copy the relevant section verbatim into the subagent prompt.

Each section is self-contained. Keep prose tight: the goal is "capture the concept" for new users without the full toolkit, not to replicate the plugin.

**Design principle**: fallbacks are degraded-but-useful, not skip-silently. Build-loop should carry knowledge of *what to look for* even when it can't run the deep validation. Every section below names specific files, grep patterns, or commands — not just "investigate carefully."

---

## web-ui — Web UI build / validation

**Standalone mode when IBR is not installed.** Build-loop cannot compute CSS values or drive a browser, but it CAN grep the code for specific violations the IBR scan would have caught. The checks below are the minimum-viable static-analysis subset.

### Design principles (Calm Precision, condensed from global `CLAUDE.md`)

- **Intent**: every visible element must help the user act, understand, decide, or recover. Remove controls, nav items, filters, charts, and options that do not work or do not serve the current workflow.
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
- **Primary action**: one core hero/primary action by default. Add multiple primary actions only when users genuinely need parallel choices.
- **Beauty in the basics**: loading, empty, error, disabled, success, and permission states must be useful and polished.

### Static grep checks (run these at Review-D Fact-Check when IBR absent)

Each check returns matches = potential violation. Not all matches are real violations — some are false positives. Review output manually; flag when confidence is high.

```sh
# 1. Gestalt — individual borders on items inside a list/map
# Look for .map() returning elements with border styles
grep -rn "\.map(" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | grep -B1 -A5 "border\|rounded-" | head -20

# 2. Touch targets — buttons/links narrower than 44px
# Catches explicit width props. Won't catch Tailwind classes without a second pass.
grep -rnE "<(button|a)\s[^>]*(width|w-[0-9])" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | grep -vE "w-(full|auto|screen|[4-9][0-9]|1[0-9]{2,})" | head -20

# 3. Interactive elements missing handlers
# <button> without onClick or type="submit" is suspicious
grep -rnE "<button[^>]*>" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | grep -v "onClick\|type=.submit.\|type=.reset." | head -20

# 4. <a> without href or onClick
grep -rnE "<a\s[^>]*>" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | grep -v "href=\|onClick=" | head -20

# 5. Missing aria-label on icon-only buttons
grep -rnE "<button[^>]*>\s*<(svg|Icon|[A-Z][a-zA-Z]*Icon)" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | grep -v "aria-label" | head -20

# 6. Status rendered as background pill (signal-to-noise violation)
# Common classes: bg-red-*, bg-green-*, bg-amber-* on small text
grep -rnE "bg-(red|green|amber|yellow|orange)-[0-9]{3}.*text-[a-z]+-[0-9]{3}" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | head -20

# 7. Hardcoded color hexes (should use tokens)
grep -rnE "#[0-9a-fA-F]{3,8}\b" --include="*.tsx" --include="*.jsx" --include="*.css" src/ app/ 2>/dev/null | grep -v "^[^:]*:[0-9]*:\s*//\|^[^:]*:[0-9]*:\s*/\*" | head -20

# 8. Non-8pt spacing (odd pixel values)
grep -rnE "(padding|margin|gap|top|right|bottom|left):\s*([0-9]+)px" --include="*.css" --include="*.scss" src/ app/ 2>/dev/null | awk -F'[:p]' '{if($4 && $4!~/^(0|4|8|12|16|20|24|32|40|48|56|64)$/) print $0}' | head -20

# 9. Console errors / warnings left in code
grep -rnE "console\.(log|error|warn|debug)" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" src/ app/ 2>/dev/null | grep -v "\.test\.\|\.spec\.\|__tests__/" | head -20

# 10. Mock data / faker / placeholder in production paths
grep -rnE "(faker|@faker-js|lorem ipsum|PLACEHOLDER|TODO:.*REAL_DATA|Math\.random\(\))" --include="*.ts" --include="*.tsx" src/ app/ 2>/dev/null | grep -v "\.test\.\|\.spec\.\|__tests__/\|fixtures/" | head -20

# 11. Dead or decorative UI promises
grep -rnE "(coming soon|not implemented|TODO|href=\"#\"|onClick=\\{\\(\\) => \\{\\}\\}|disabled)" --include="*.tsx" --include="*.jsx" src/ app/ 2>/dev/null | grep -v "\.test\.\|\.spec\.\|__tests__/" | head -20
```

### File-check matrix

After greps, verify these files exist and have the right shape:

| Check | File pattern | What it needs |
|---|---|---|
| Accessible landmarks | `app/layout.tsx` or `src/App.tsx` | `<main>`, `<nav>`, `<header>`, `<footer>` present |
| Skip-to-content link | Same | `<a href="#main">Skip</a>` before nav |
| Focus styles | global CSS | Explicit `:focus-visible` rule, not `outline: none` without replacement |
| Keyboard shortcuts | Anywhere | `onKeyDown` handlers on non-button interactive elements (divs, spans with roles) |

### Runtime validation (still available without IBR)

If the dev server is running, also do:

1. `curl -s -o /dev/null -w "%{http_code}" <url>` — page loads
2. `curl -s <url> | grep -c '<meta name="viewport"'` — must be 1 (viewport tag present, mobile-responsive)
3. User explicit manual check: tab through interactive elements, watch console, screenshot

Report any failures in Review-D with file path + line number. Flag with `⚠️ static-analysis only — install IBR for computed-CSS verification`.

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

## architecture — Blast-radius and impact analysis

**Standalone mode when NavGator is not installed.** Build-loop cannot build a full dependency graph, but it CAN produce a useful approximation from git history, filesystem layout, and import greps. Less accurate than NavGator's AST-aware scan; good enough to scope Plan correctly.

### Assess step (before Plan)

Run these in order. Output goes to `.build-loop/state.json.architecture.standalone`.

```sh
# 1. Changed files (from the goal's scope or current diff)
CHANGED=$(git diff --name-only origin/main..HEAD 2>/dev/null || git diff --name-only HEAD 2>/dev/null)
echo "$CHANGED"

# 2. Layer classification by conventional directories
# Map each changed file to a layer heuristic
echo "$CHANGED" | while read f; do
  case "$f" in
    src/db/*|src/models/*|prisma/*|*migrations*) echo "db: $f" ;;
    src/api/*|app/api/*|pages/api/*|src/routes/*) echo "backend: $f" ;;
    src/components/*|app/*/page.tsx|app/*/layout.tsx|pages/*) echo "frontend: $f" ;;
    src/workers/*|*queue*|*job*) echo "queue: $f" ;;
    src/lib/*|src/utils/*|src/shared/*) echo "shared: $f" ;;
    *.test.*|*.spec.*|__tests__/*) echo "test: $f" ;;
    *) echo "other: $f" ;;
  esac
done

# 3. 1-hop dependents — who imports these files?
# For each changed file, grep for its module specifier across the repo
echo "$CHANGED" | while read f; do
  MODULE=$(echo "$f" | sed 's|^src/||; s|\.tsx\?$||; s|\.jsx\?$||; s|/index$||')
  IMPORTERS=$(grep -rlE "from ['\"][@~/]*${MODULE}(/|'|\")" src/ app/ 2>/dev/null | grep -v "^$f$" | head -10)
  echo "$f → imported by: $(echo $IMPORTERS | tr '\n' ' ')"
done

# 4. Hotspot detection — files with high git churn (proxy for high fan-in)
# Top 10 most-changed files in the last 100 commits
git log --pretty=format: --name-only -100 2>/dev/null | sort | uniq -c | sort -rn | head -10

# 5. Circular-import smell — TypeScript compiler already catches these on `tsc --noEmit`
# Run the type check and look for "Cannot find module" or "circular" in output
# (Delegate to Review-B's type check grader; just note this is where cycles surface.)
```

### Risk flags

Emit a risk flag when:
- Changed files cross ≥3 layer classifications (e.g. frontend + backend + db in one build) — high blast radius
- Any changed file appears in the top-5 hotspots from check #4 — concentration risk
- 1-hop dependent count > 10 for any single changed file — fan-out concern
- Changed files include both `src/db/` and `src/components/` without going through `src/api/` — possible frontend-direct-db layer violation

### What this fallback cannot do (flag these as gaps)

- Transitive (2-hop+) dependency tracing — NavGator's `graph.json` required
- LLM prompt mapping (`navgator llm-map`) — needs the AST-aware scanner
- Lessons/recurrence matching (`.navgator/lessons/`) — this is NavGator-specific storage
- Post-change architectural rule enforcement (`navgator rules`) — requires full component classification

When any of the above would materially affect the build (e.g. large refactor touching 20+ files, or a build that edits product LLM prompts), recommend installing NavGator rather than pushing forward with the fallback. Note this in the Review-F report as `⚠️ NavGator would improve confidence here`.

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

**Always note LLM cost** in the Review-F report when this fallback runs. Flag that installing `scraper-app` would eliminate the token spend.

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

**Standalone mode when `claude-code-debugger:debugging-memory` is unavailable.** No verdict classifier, no cross-session training. Just a file-grep of this project's prior builds.

### Query procedure

Extract key tokens from the current failure (error class, function name, file path, distinctive noun). Then:

```sh
SYMPTOM="<your symptom string>"
# Pull the 3-5 most distinctive words from SYMPTOM
TOKENS=$(echo "$SYMPTOM" | tr ' ' '\n' | grep -E '^[A-Z][a-zA-Z]+$|^[a-z_]+[A-Z][a-zA-Z]+$|Error|Exception|timeout|undefined' | head -5)

# Search local project history
for T in $TOKENS; do
  grep -R -l "$T" .build-loop/issues/ 2>/dev/null
  grep -R -l "$T" .build-loop/feedback.md 2>/dev/null
  grep -R -l "$T" .bookmark/ 2>/dev/null
done | sort -u
```

### Degraded verdict (4 states, same shape as debugger-bridge)

| State | Match rule | Action |
|---|---|---|
| `LOCAL_HIT_EXACT` | At least one file contains the full symptom string (case-insensitive substring match) | Read that file; adapt its recorded fix as the Iterate plan. Not direct-apply. |
| `LOCAL_HIT_PARTIAL` | ≥2 tokens co-occur in the same file | Reference the file in the Iterate plan; investigate normally |
| `LOCAL_WEAK` | 1 token match only | Note reference, investigate normally |
| `LOCAL_NO_MATCH` | No files contain any tokens | Standard Iterate; write a new `.build-loop/issues/<slug>.md` after resolution |

No confidence score (no classifier). No cross-project lookup. No automatic training signal back to the source — this is strictly read-only memory for one project.

### Storage (write side)

After resolving a failure, append to `.build-loop/issues/YYYY-MM-DD-<slug>.md`:

```
# <one-line title>

**Symptom**: <error string as it appeared>
**Root cause**: <what was actually wrong>
**Fix**: <diff summary or description>
**Files**: <paths touched>
**Tags**: <layer>, <component>, <pattern>
```

Future builds will grep this file. Installing `claude-code-debugger` promotes this to a classified, cross-project, ranked memory — but the file-grep works standalone.

---

## logging-fallback — Observability when claude-code-debugger absent

**Standalone mode when `claude-code-debugger:logging-tracer` is unavailable.** Minimum-viable Tier-1 structured logging per language. Covered in `skills/logging-tracer-bridge/SKILL.md` §"Fallback when upstream is absent" — the bridge already contains the standalone code. This fallback section exists only to point at the bridge:

> For zero-dep structured-JSON logging in Node, Python, Go, or Rust, see `skills/logging-tracer-bridge/SKILL.md`. The bridge's Tier-1 fallback is a 5-8 line helper per language that writes to stderr and respects a `DEBUG_TRACE=1` env gate. This is the standalone path; nothing more elaborate is available without the debugger plugin.

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

For the Review-F scorecard: governing thought = did the build meet the goal; key lines = the scoring criteria; support = evidence rows.

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
