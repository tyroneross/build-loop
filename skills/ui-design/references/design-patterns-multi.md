# Multi-Pattern Token Framework — Feasibility Draft

> **DEPRECATION SHIM (one release cycle).** Canonical source migrated to the `ui-guidance` plugin → load via `Skill("ui-guidance:multi-pattern-tokens")`. This file remains in build-loop as a transition aid; future updates land in the plugin.
>
> **Status:** DRAFT — exploration only. Not active, not loaded by ui-design. Decision pending the Feasibility verdict at the bottom.
>
> **Source attribution:** Adapted from Google Labs' DESIGN.md spec at <https://github.com/google-labs-code/design.md> (fetched 2026-05-26 from the raw README at `main`, 337 lines). DESIGN.md proposes a YAML-front-matter + markdown-prose hybrid: tokens are normative, prose tells agents *why* they exist. This document explores whether the same hybrid can extend to **multi-pattern** tokens — i.e., one design language expressing different concrete values per form factor (mobile / tablet / web / watch) without forking the system.

## The motivating problem

Calm Precision currently states sizes, spacing, and color in single concrete values:

- `44px mobile, 24px desktop touch` (two values, hard-coded into the principle line).
- `4.5:1 contrast` (one value, universal).
- `8pt grid` (one value, universal).
- `Title (14-16px bold) → Description (12-14px) → Metadata (11-12px muted)` (ranges, but flat — no per-form-factor resolution).

When the same agent ships across iPhone, iPad, macOS, and web, it has to *interpret* these ranges every time. A typography token like `body-md: 16px` means one thing in a tablet reading view, another in a watch glance, another in a desktop dashboard. Today this interpretation lives in the agent's head — re-derived per surface, drift-prone, easy to get wrong on the small or far-from-default surface.

A multi-pattern framework lets the design system *declare* the resolution once, as a structured token, and ship the same `DESIGN.md` everywhere.

## The format

Three patterns are proposed. Each extends DESIGN.md's existing YAML-front-matter shape — none of them require a new top-level concept; they expand what a token *value* can be.

### Pattern 1 — `pattern: scale`  (responsive scalar)

A single token resolves to different concrete values per named breakpoint or form factor. The consumer picks the value based on its render context.

```yaml
typography:
  body-md:
    pattern: scale
    fontFamily: Public Sans
    fontSize:
      watch: 13px       # 38mm-44mm Apple Watch — small viewport, denser scale
      mobile: 16px      # iPhone default
      tablet: 17px      # iPad — bumped one step for reading-distance
      web: 16px         # desktop browser, 1280px+
    lineHeight: 1.5     # universal — does not vary
```

Consumer behavior: an iOS app picks `mobile`, an iPad app picks `tablet`, a watch app picks `watch`. Tokens that don't declare a `pattern: scale` shape fall back to a single value (back-compat with DESIGN.md alpha).

### Pattern 2 — `pattern: ladder`  (semantic step with named rungs)

A token is a *step* on a named ladder rather than a fixed value. The ladder definition lives once at the top of the doc; tokens reference rungs by name. Lets a designer change "what `space-loose` means on tablet" once and have every consuming token pick it up.

```yaml
ladders:
  spacing:
    tight: { mobile: 4px, tablet: 6px, web: 8px }
    snug:  { mobile: 8px, tablet: 12px, web: 12px }
    loose: { mobile: 16px, tablet: 24px, web: 24px }
    roomy: { mobile: 24px, tablet: 32px, web: 40px }

components:
  card:
    pattern: ladder
    padding: "{ladders.spacing.snug}"   # resolves to 8/12/12 depending on surface
    gap:     "{ladders.spacing.tight}"  # resolves to 4/6/8
```

Compared to Pattern 1, Pattern 2 separates *naming* from *value*. The cost is one extra layer of indirection; the benefit is changing all `snug` paddings across the system in one edit.

### Pattern 3 — `pattern: condition`  (state-driven token)

A token resolves to different values based on a runtime condition the consumer reports — not just form factor. Useful for accessibility-sensitive properties (contrast, motion, density), dark/light mode, and reduced-motion preferences.

```yaml
colors:
  primary-text:
    pattern: condition
    default: "#1A1C1E"
    when:
      colorScheme=dark: "#F2F2F2"
      contrastLevel=high: "#000000"     # WCAG AAA-locked surface
      reduceTransparency=true: "#1A1C1E"

motion:
  card-enter-duration:
    pattern: condition
    default: 240ms
    when:
      reduceMotion=true: 0ms
      density=compact: 160ms
```

Consumers report their condition bag at render time; the token resolver picks the most-specific match (with a documented precedence order).

### Pattern 4 — `pattern: composite` (cross-token resolution)

A component token resolves multiple sub-tokens together to enforce internal consistency. The composite shape is the unit of legal variation, not the individual sub-tokens.

```yaml
components:
  button-primary:
    pattern: composite
    variants:
      mobile:
        backgroundColor: "{colors.tertiary}"
        textColor: "{colors.on-tertiary}"
        padding: 12px
        minHeight: 44px        # iOS touch target
      tablet:
        backgroundColor: "{colors.tertiary}"
        textColor: "{colors.on-tertiary}"
        padding: 14px
        minHeight: 48px
      web:
        backgroundColor: "{colors.tertiary}"
        textColor: "{colors.on-tertiary}"
        padding: 10px 16px
        minHeight: 36px        # desktop hover-driven, not touch
```

Pattern 4 is the most opinionated and the most useful for the touch-target case: the touch-vs-pointer split is a *composite* concern (size + padding + minHeight all change together), and expressing it as one variant block makes the intent legible.

## Calm Precision compatibility

The four patterns are extensions, not replacements. Calm Precision principles map directly:

| Calm Precision rule | Today | Multi-pattern expression |
|---|---|---|
| `44px mobile, 24px desktop touch` | inline two-value statement | Pattern 4 (composite `button-primary` with mobile/web variants) |
| `Title 14-16px bold → Description 12-14px → Metadata 11-12px` | range, agent interprets | Pattern 1 (scale by form factor) or Pattern 2 (ladder rungs) |
| `4.5:1 contrast` | universal | Pattern 3 (condition: high-contrast surface picks AAA values) |
| `8pt grid` | universal | unchanged — no pattern needed |
| `Status = text color only, no background badges` | universal rule | unchanged — qualitative, not a token |

Two non-negotiables stay outside the framework because they are *behavioral*, not numeric: "No fake buttons / backend must exist before UI" and "Real data default — mock requires explicit permission". The framework expresses values, not data contracts.

## Implementation risk

| Risk | Severity | Mitigation |
|---|---|---|
| Token resolution becomes a build-time dependency every consumer needs | **High** | Ship a stdlib-only Python resolver (≤200 LOC) in `ui-design/scripts/` so consumers can shell out. No JS toolchain required for read-only consumers. |
| Conditions can interact (dark + high-contrast + reduceMotion all true) | **Medium** | Declared precedence order in spec; resolver picks most-specific then falls back. Test matrix at the resolver level. |
| Naming sprawl — ladders + patterns + components overlap | **Medium** | One pattern per use case; document a decision tree (form factor → scale; cross-token consistency → composite; runtime state → condition; named tiers → ladder). |
| Drift from DESIGN.md upstream — they may evolve the spec | **Medium** | Keep our extension namespaced as `pattern: <name>`; if upstream ships native support we can deprecate ours. Track the upstream repo at v0.x and re-evaluate on each minor bump. |
| Tokens become opaque at the source — readers can't tell what value an agent will pick | **Medium** | Resolver MUST emit a "resolved view" markdown file as a build artifact, showing what each token resolves to on each declared form factor. Reviewable in PRs. |
| Adoption cost across 8 existing apps is non-trivial | **Low** | Pattern adoption is opt-in per project; existing single-value tokens keep working. |

## Build-time complexity

- **Resolver**: ~200 LOC Python, stdlib-only (yaml parsing via `PyYAML` already in build-loop deps). Pure function: `(token_path, form_factor, conditions) → value`.
- **Linter extension**: extend DESIGN.md's existing `lint` command to recognize the four new `pattern:` values and validate their internal shape (e.g., a `scale` token MUST declare at least 2 form factors).
- **Diff tool**: extend `diff` to compare resolved values per form factor, not just raw token values — otherwise diffs would miss the case where the `mobile` value changed but `tablet` didn't.
- **CI integration**: every consuming app's CI runs the resolver and asserts the resolved view matches a checked-in snapshot. Drift becomes a PR-time signal, not a runtime surprise.

Estimated effort to ship a v0.1 of all four patterns + resolver + linter extension: M (medium). Estimated effort to retrofit Calm Precision's three numeric rules: S (small) — three token files.

## Feasibility verdict

**Verdict: ITERATE.** The pattern shapes are compatible with Calm Precision and address a real drift problem (the touch-target rule, the typography range rule, the dark-mode color rule). But the implementation risk concentrates in two places — *token opacity at the source* (readers can't see what an agent will pick) and *condition-interaction ambiguity* — that would burn trust if shipped without a resolved-view artifact and a precedence test matrix.

**Recommended next step (NOT executed in this build):** prototype Pattern 4 (composite) only, on Calm Precision's touch-target rule, in one app (TruePace — iPad-primary with iPhone secondary, so it actually exercises the variant resolution). Measure: does the composite token reduce the per-form-factor interpretation work an agent has to do? If yes, expand to Pattern 1. If no, the abstraction isn't paying for its complexity.

**Rejected today:** shipping all four patterns to active use simultaneously. The composite case is the highest-value, lowest-risk entry point; the other three patterns should be gated on the composite prototype's outcome.

**Not adopted, not rejected:** patterns themselves are sound; the question is sequencing and prototype evidence, not design correctness. This document stays as a draft until a single-pattern prototype confirms or denies the value claim.

---

*Drafted in build-loop run 2026-05-26 against the universal-design enrichment intent. Re-evaluate when the Pattern 4 prototype lands or when DESIGN.md upstream releases a `pattern:` shape natively.*
