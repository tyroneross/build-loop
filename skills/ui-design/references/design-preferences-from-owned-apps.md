<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

> **DEPRECATION SHIM (one release cycle).** Canonical source migrated to the `ui-guidance` plugin → load via `Skill("ui-guidance:preferences")`. This file remains in build-loop as a transition aid; future updates land in the plugin. *(Renamed from `design-preferences-evidence` in ui-guidance v0.2.0.)*

# Design Preferences — Sourced from Owned Apps

> Sourced from `.build-loop/design-evidence/*.md` (8 apps scanned 2026-05-26). Every claim in this file traces to a specific evidence file. The evidence files are the ground truth — when this doc and an evidence file disagree, the evidence file wins.
>
> **Primary preferred references** (per intent.md): Sample Voice iOS, Sample Timer (iPad primary), Sample Reader, Sample Product App.
> **Secondary / directional** (note what works AND what user dislikes): Secrets Vault macOS, Sample Decision App, Sample Offline iOS, Sample Onboarding App.

---

## 1. Typography: tokenized three-line hierarchy, not inline pixel sizes

**Universal non-negotiable:** *"Visual hierarchy is one-glance recoverable"* (alt doc §2.12) + Calm Precision's *"Title (14-16px bold) → Description (12-14px) → Metadata (11-12px muted)"*.

**What works (primary apps):**
- **Secrets Vault** — verbatim CP 6.4.1 implementation in `VaultTypography.swift` (evidence: `secrets-vault-macos.md`). Token comment in source code: *"L1: Title 15px medium high contrast → L2: Description 13px regular medium contrast → L3: Metadata 11px regular low contrast."* This is the textbook reference.
- **Sample Timer App** — `TextRole` enum (`Theme.swift:689-721` per `sample-timer.md`) ladders seven named roles (display/title/headline/subheadline/body/caption/micro) with explicit baseSize + defaultWeight per rung. Tokens cite role purpose: *"display: 30pt — single largest element on a screen."*
- **Sample Voice App** — nine named typography tokens (`Theme.swift:31-35,70-75` per `sample-voice-ios.md`) including `fontTabular` (10pt mono-equivalent for numerics) — a use-case that single-purpose token sets miss.

**Anti-pattern (secondary apps):**
- **Sample Onboarding App** — typography is **inline pixel sizes scattered across call sites**: `.font(.system(size: 34))`, `.system(size: 20)`, `.system(size: 18)`, `.system(size: 16)`, `.system(size: 15)`, `.system(size: 14)`, `.system(size: 13)`, `.system(size: 12)` — all in `SetupView.swift` alone (evidence: `sample-onboarding-app.md`). Cannot grep for "title size." Cannot bump the scale once. The ladder is not a ladder; it is a pile.

**Preference recorded:** **define a typography token enum (TextRole-style) before writing the first view.** Inline pixel sizes are tolerated only when the role doesn't yet exist in the enum — and that gap becomes a follow-up to extend the enum, not a permanent state.

---

## 2. Color: single accent, hierarchical text, status-as-text

**Universal non-negotiable:** *"Visual hierarchy is one-glance recoverable [...] If color is removed, the hierarchy still holds"* (alt doc §2.12) + Calm Precision's *"Status = text color only, no background badges."*

**What works (primary apps):**
- **Sample Product App** — strictest single-accent enforcement in inventory: `--primary === --accent === --ring === #f0b65e` (`index.css:18,22,26` per `sample-product.md`). The entire palette is warm-monochromatic; the only "second color" is `--success: #7bc67e` (muted sage) and `--destructive: #e06356` (warm red-orange, not pure red — palette discipline holds even on error).
- **Sample Reader** — explicit semantic separation: `--color-error-bg` and `--color-error-border` exist for tinted alert containers, but they are tokens distinct from `--status-error` (the foreground text color). Container surfaces are gated to reading flow where text-color-only would underread (`sample-reader.md` cites the divergence with rationale).
- **Secrets Vault** — three-tier text contrast (`textPrimary / textSecondary / textMuted` = stone-900 / stone-600 / stone-400 in light; stone-100 / stone-300 / stone-450 in dark, per `secrets-vault-macos.md`). Each tier maps directly to L1/L2/L3 of the typography ladder — text size + text contrast move together.

**Anti-pattern (secondary apps):**
- **Sample Decision App theme-toggle pattern** — three switchable themes (F default, A "Case File," B "Conversation") via `[data-theme="A"|"B"]` on `<html>` (evidence: `sample-decision-app.md`). Each theme owns the same 6 token names but different brand colors. *Works for*: keeping semantic meaning constant across visual presentation. *Fails for*: brand identity — a brand that can become blue or terracotta or red is brand-fungible. Likely user-dislike: the existence of the toggle dilutes the canonical voice (Theme F).
- **Sample Reader tinted error containers** — divergence from Calm Precision noted above. **Not always a flaw** — the sample reader justifies it by reading-flow density, and the divergence is contained to status containers — but it is a divergence, and absent a similar rationale, default to text-color-only.

**Preference recorded:** **one accent color per app. Status uses text color first; tinted containers only when reading-flow density justifies and the divergence is documented in the token file's comments.**

---

## 3. Touch targets: tokenized at component layer, never per-view

**Universal non-negotiable:** *"Interaction targets match input precision"* (alt doc §2 visual-craft inheritance) + Calm Precision's *"44px mobile, 24px desktop touch."*

**What works (primary apps):**
- **Sample Product App** — `.btn-primary` class explicitly enforces `min-h-[44px] min-w-[44px]` at the **component layer** (`index.css:52` per `sample-product.md`). Every primary button passes the Calm Precision touch target without any per-view code.
- **Sample Voice App** — primary CTA is 52pt height (`HomeView.swift:92` per `sample-voice-ios.md`), exceeds the 44pt floor, and the tab bar uses a 100pt reserve token (`Theme.tabBarReserve`) so list items never clip behind the iOS 26 floating bar.

**Cited Calm Precision compliance via system defaults:**
- **Sample Offline iOS** — uses Apple system `List` rows and `.plus` button — both meet 44pt via Apple defaults. Demonstrates that *"system primitives over visual fakes"* (alt doc §2.14) can be the touch-target win — but only when the app doesn't need brand-distinct controls.

**Preference recorded:** **encode touch-target minimums in the component class itself, not in per-view padding math.** When using system primitives, prefer them — Apple has already done the work.

---

## 4. Responsive form factor: token-scaled, not per-platform fork

**Universal non-negotiable:** *"Content fits its container, or the container fits the content"* (alt doc §2.11).

**What works (gold standard — Sample Timer App):**
- Single typography hierarchy multiplied by `\.viewportScale` env var (`sample-timer.md` cites `Theme.swift:689-836`):
  - iPhone compact size class: 1.0×
  - iPad / Mac regular-width: 1.5×
  - Mac live window resize: `max(0.85, min(1.5, shorterEdge / 600))`
  - iPad sheets: additional 1.15× uplift over canvas (1.5 × 1.15 = 1.725×)
- Watch palette has its own warm/cool dual mode: cool primary palette when display is active, warm `Dim` variants when `isLuminanceReduced == true` (Always-On Display) — circadian-safe >560nm tones.
- Mode accents (timer / flow / adaptive / break) ship as `GradientColorSet` with **7 distinct color slots** per mode (core + 5-stop dark gradient + 5-stop light gradient + blob primary + blob secondary + warm target + ring end).

**What doesn't work (anti-pattern — none in inventory, but absence noted):**
- No app in the inventory ships separate iPhone and iPad themes as forked codepaths. The closest is Sample Timer App's `#if os(watchOS)` block which uses a separate warm-cool palette for watch — but that is platform-isolated, not a fork.

**Preference recorded:** **viewport-scale tokens that read a single env var. iPad is the iPhone hierarchy × 1.5; Mac is the iPad math but live-window-resize-aware; Watch is its own palette but the same role names.** Forking iPhone and iPad theme files is the failure mode; the multi-pattern framework draft (`design-patterns-multi.md`) Pattern 1 / Pattern 4 are the formal expression of this preference.

---

## 5. Motion: respect-reduce-motion is mandatory, anti-flicker is intentional

**Universal non-negotiable:** *"Motion serves comprehension, not decoration"* (alt doc §2 visual-craft inheritance).

**What works (primary apps):**
- **Sample Decision App** — `@media (prefers-reduced-motion: reduce)` overrides all animations to 0.01ms at the globals level (`app/globals.css:65-71` per `sample-decision-app.md`). Single CSS rule covers the whole app.
- **Sample Voice App** — `@Environment(\.accessibilityReduceMotion)` honored on HomeView; first-time onboarding animation gated to once per AppStorage flag (`HomeView.swift:16,47` per `sample-voice-ios.md`).
- **Secrets Vault** — stagger animation capped at 400ms total (`60ms × N items, max 400ms`) — prevents the long-list "wave" anti-pattern (per project CLAUDE.md cited in `secrets-vault-macos.md`).
- **Sample Onboarding App** — StatusBanner's `phase label itself stays visible briefly after a stage transition so the user sees the most recent phase without flicker` (verbatim from `StatusBanner.swift` per `sample-onboarding-app.md`). Anti-flicker is a documented design decision, encoded in code comments.

**Preference recorded:** **`prefers-reduced-motion` / `accessibilityReduceMotion` must be respected by default, not opt-in. Stagger animations have a total-time cap, not just a per-item interval. Anti-flicker behavior is a first-class design concern, not an afterthought.**

---

## 6. Voice + error UX: verb+object, what→why→fix, calm degradation

**Universal non-negotiable:** *"When something fails, explain what happened, preserve context, give a next step"* (alt doc §1.5 Recover) + *"Resilient to imperfect input"* (alt doc §2.9).

**What works (primary apps):**
- **Secrets Vault** — explicit three-part error pattern named in project CLAUDE.md (cited `secrets-vault-macos.md`): *"Errors: what → why → fix pattern."* Plus voice rule: *"Verb+Object labels, contextual loading ('Deriving encryption key…')"* — loading messages name the actual operation.
- **Sample Offline iOS** — *"Errors surface inline (red footnote) without removing cached rows — offline still shows the last good list"* (`CampsListView.swift` code comment per `sample-offline-ios.md`). On error, the cache is preserved — graceful degradation in action.
- **Sample Voice App** — `Haptics.impact(.light)` paired with state change on every CTA tap (`HomeView.swift:81` per `sample-voice-ios.md`) — action feedback via haptic + visible state, not just one or the other.

**Preference recorded:** **errors are calm and informative (what / why / fix); they never wipe state; loading is named ('Deriving encryption key…' not 'Loading…'); actions get both haptic + visual confirmation on mobile.**

---

## 7. Anti-pattern roundup (avoid these by default)

| Anti-pattern | Source | Why it fails |
|---|---|---|
| Inline pixel-size typography | `sample-onboarding-app.md` | Ladder not enforceable, scale bump not greppable, accessibility uplift impossible at scale |
| Theme-toggle for brand color | `sample-decision-app.md` | Brand fungibility — *"design-toggle app"* not *"Decision Doctor"* |
| Tinted error containers without rationale | (sample reader gets a pass with rationale) | Default fails Calm Precision's text-color-only rule; needs reading-flow density justification when used |
| System-default with no brand voice | `sample-offline-ios.md` | Indistinct from any other camp-management list; appropriate for offline-first utility, inappropriate for a brand-led product |
| Off-grid spacing without flag | (Sample Timer App handles this well — off-grid values are noted as "exotic" in code comments per `sample-timer.md`) | Drift cause; the discipline is the comment, not the value |

---

## 8. What this means for new builds

A new app in this ecosystem should, in priority order:

1. **Token files first** — `Theme/Colors`, `Theme/Typography` (or `index.css`-equivalent for web) before the first view is written. Use Sample Voice App / Sample Timer App / Secrets Vault as templates.
2. **One accent. Single. Don't theme-toggle.** Sample Product App is the strictest example.
3. **Three-line text hierarchy** baked into the typography enum. Secrets Vault's `VaultTypography.title/.description/.metadata` is the textbook.
4. **Touch targets at the component layer.** Sample Product App's `.btn-primary` is the example.
5. **`prefers-reduced-motion` / `accessibilityReduceMotion` from day one.** The sample decision app's single-CSS-rule approach is the cheapest implementation.
6. **Errors are calm, informative, non-destructive.** The sample offline app's offline-preserves-cache + Secrets Vault's what→why→fix pattern combine into a single rule.
7. **Multi-form-factor via viewport-scale tokens, not forked themes.** Sample Timer App's `\.viewportScale` is the reference; the multi-pattern framework draft (`design-patterns-multi.md`) is where this becomes formal if/when prototyped.

When deviating: name the deviation in the token file's comments, the way Sample Timer App flags off-grid spacing and Sample Reader flags tinted error containers. The discipline is the comment.

---

## 9. Live-Capture Addendum (2026-05-26)

Live IBR captures on the 4 primaries surfaced texture observations source-read could not:

- **Tokens extend to haptics.** Sample Voice App's `HapticVocabulary.swift` (`.confirm/.reward/.warn/.progress/.selection`) is a semantic layer above UIKit feedback generators, parallel to `Theme.fontDisplay` above `Font.system(...)`. Treat haptics as first-class tokens on mobile. Evidence: `sample-voice-ios.md` §Live IBR Capture.
- **Selection signaling is NOT consistent across primaries.** Sample Voice App uses solid-fill on selected (filter pills, tab); Sample Timer App uses 1pt border + glyph check on selected mode card. Both legitimate; choose per surface density and decision weight, not by a single rule. Evidence: `sample-voice-ios.md` + `sample-timer.md`.
- **Empty states diverge by intent.** Sample Voice App floats SF Symbol + copy (first-use); Sample Timer App renders milestone ladder (unlock-progression); Sample Product App uses text label (transient load). Match vocabulary to user reason-for-emptiness.
- **AI-assist lives inside input when input is the primary action.** Sample Product App's sparkle "Enhance" sat inside the textarea (`sample-product.md` §Live IBR Capture, 2026-05-03). Generalizable. (As of 2026-05-26 refresh, Sample Product App's landing has been redesigned to headline+CTA-first; the input-with-Enhance pattern moved one click deeper. Pattern still holds, citation is historical.)
- **Brand-identity-in-chrome is universal, treatment is product-specific.** All 4 primary apps have distinct brand chrome — no two alike. System-default brand chrome is the explicit deviation.
- **Two-speed motion on a single control.** Sample Product App primary CTA: `transition: background 0.2s, transform 0.15s` — geometry settles 50ms faster than color for snappier press feedback while color carries texture. When a control shifts both color and shape, tune per channel. (`sample-product.md` §Fresh Live IBR Capture)
- **Error-state restraint mirrors main-app restraint.** Sample Product App 404 = 5 elements (icon, title, body, CTA, card). No illustration, no error code, no search. Empty space carries the signal. Pattern: error/empty states inherit the app's restraint discipline; do not overcompensate.
- **Friction-removal microcopy has a stable shape:** short / two-clause / bulleted (`·`) / muted ≤12pt, placed under a CTA when account/cost/lock-in friction is the user concern. Sample Product App uses the same recipe on the landing CTA (`No account required · Free to try`) and the auth card (`No account · Groq Llama 3.3`).

These supersede any prior synthesis claim that read selection-signaling or empty-state vocabulary as universal. The `evidence-capture-policy.md` reference codifies why source-read alone could not produce these observations.
