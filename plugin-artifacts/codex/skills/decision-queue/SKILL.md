---
name: decision-queue
description: "Turn pending decisions — Operations Center tasks with status needs_input, a backlog of open questions, anything blocked on the user's judgment — into one interactive page the user answers inline and Claude reads back later. Not for a one-off static report with no response capture; use a plain Artifact instead. Each decision gets a card carrying the choice, why it needs the user, its impact, the options, and a recommendation. Triggers: 'show me the decisions waiting on me', 'what needs my input', 'make a decision queue', 'launch a ui for these open questions'."
user-invocable: false
companion_assets:
  - assets/template.html — tested, working page (styling, save/response plumbing, self-publish logic). Copy and adapt; never regenerate from scratch.
  - scripts/regen_template_constants.py — regenerates the HEAD_HTML / SAVE_BAR_HTML self-publish constants from the authored markup. Run it after ANY CSS or save-bar edit; never hand-sync the two copies.
  - references/example-large-queue-batching.md — second worked example (2026-08-26, PersonalLLMWiki planner backlog): the large-queue variant, where items are classified into a few claims and ruled as batches instead of one card per item. Read it before building for a queue of 50+ items.
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Decision Queue

Build an interactive Artifact that lists every pending decision as a card —
decision, why it needs the user, impact, options, recommendation, a response
control, and a save button — so the user can answer at their own pace and
Claude can read the answers back on request. Built once (2026-08-25) for
Operations Center's `needs_input` queue; the pattern generalizes to any set
of open questions the user names.

## When to use

- User asks what's waiting on their input, across Operations Center, a
  backlog, or any other queue of blocked decisions.
- User wants to *answer* those decisions somewhere durable, not just read
  a report — the defining feature over a plain summary is the save/respond
  loop.
- Do not use this for a single decision (just ask) or for content with no
  response to capture (use a plain Artifact via `artifact-design`).

## Two shapes, chosen by queue size

**One card per decision** is the default and everything below describes it. It
works while the decisions are few and genuinely distinct.

**Batch-claim triage** is the variant for a large queue. Above roughly fifty
items, a page of one card per item reproduces the overwhelm that made the user
ask for a page. Classify the items into a few classes instead, give each class
one falsifiable claim plus the counted evidence for it, and let one ruling
close the whole class with a drill-in for auditing and per-item override.

The test between them is one question: **can you write a single sentence that
is true of thirty of these items?** If yes, build the batch variant and read
`references/example-large-queue-batching.md` first — it carries the pyramid
structure the claims need, the classification trap that cost a rebuild, the
flat-control CSS the user requires, and a save round-trip test worth copying.
If no, you do not have batches; build one card per decision.

## Workflow

1. **Gather the decisions.** For Operations Center: `mcp__operations-center__list_tasks`
   filtered to `status: needs_input`. The result is often too large for the
   tool response and gets saved to a file — read it with Python/jq, don't
   try to fit it inline. **Landmine:** `get_task` takes the FULL task id;
   the short 8-char ids shown in the Operations Center startup-hook digest
   are prefixes and `get_task` returns `null` for them. Use `list_tasks`
   and filter by prefix match instead.

2. **Synthesize each item**, one card's worth of fields:

   | Field | What it answers | Note |
   |---|---|---|
   | `decision` | What action is on the table | Full sentence, not a summary of the situation |
   | `why` | Why THIS needs the user, not an agent | If the source record has no explicit reason, infer from spec/classification and set `whyInferred: true` — the template renders an "inferred" disclaimer, so the honesty marker is never lost |
   | `impact` | What actually changes — app, user, team | Concrete; cite real numbers from the source record when they exist |
   | `options` | 2–4 real choices, lettered A/B/C… | Include the honest cost of "defer"/"reject", not a straw man |
   | `recommendation` | Which option and why | Name the actual tradeoff, don't just restate the option |

   Write in full sentences with clear predicates — this is a decision
   record the user acts on, not a bullet fragment.

3. **Load `artifact-design`** (required — even though the template supplies
   a working visual treatment, the design-pass discipline still governs
   copy calibration: eyebrow/title/lede text, card language, and whether
   this dataset actually warrants the dashboard treatment over something
   lighter).

4. **Copy `assets/template.html`** to the scratchpad — never hand-roll the
   self-publish plumbing from scratch; see "The one rule that matters"
   below for why that's expensive to get right. Strip the leading `<!-- -->`
   authoring comment block (it must not ship). Edit only the CONTENT ZONE:

   ```js
   window.__META__ = { eyebrow, title, lede, summaryCells, footer };
   window.__ITEMS__ = [ { id, num, opened, touched, repo, classChip,
     typeLabel, priority, title, decision, why, whyInferred, impact,
     options, recommendation, selected: null, comment: "", respondedAt: null }, ... ];
   ```

   Leave everything inside `<script id="app-script">` untouched.

5. **Verify before publishing** — cheap and catches real bugs:

   ```bash
   node --check <extracted app-data + app-script>   # syntax
   # render META + ITEMS through the real cardHtml/renderBody functions
   # (see git history of this skill's authoring session for the exact
   # extraction snippet) and confirm it returns non-empty HTML with no
   # thrown exception
   grep -n "document.head.innerHTML\|getElementById(\"save-bar-shell\").outerHTML" *.html
   # any match OUTSIDE an explanatory comment is the landmine below — fix it
   ```

6. **Publish** with `Artifact({ file_path, capabilities: {artifact: {}}, title, description, favicon })`.
   The `artifact` capability is what lets the page save its own responses —
   without it the save button has nothing to call. Load `artifact-capabilities`
   if this is a fresh session that hasn't already loaded it.

7. **Read answers back** on request: `Artifact({ action: "read", url })`.
   The saved HTML contains the string `window.__ITEMS__` **twice** — once
   as the real, live `<script id="app-data">` content, and once more as a
   JS string literal inside `buildDocument()`'s own source (which the
   self-publish logic captures verbatim so the republished page stays
   functional). Parse the **first** occurrence, at the top of the file,
   not whichever regex match comes back first if your extraction spans
   the whole document carelessly.

## The one rule that matters: never capture the live DOM for self-publish

The template's `HEAD_HTML` and `SAVE_BAR_HTML` are **hardcoded JS string
constants**, not `document.head.innerHTML` or `.outerHTML` reads. This is
not a style preference — it was a real, shipped, user-visible bug
(2026-08-26): the claude.ai artifact viewer injects its own bootstrap
script into `<head>` before the page's own script runs. Capturing
`document.head.innerHTML` at load time sweeps that injected script up
alongside the page's own `<title>`/`<link>`/`<style>` and bakes it into
whatever gets saved. On the next load the viewer injects a **second**,
fresh copy of its own bootstrap on top of the stale one — two competing
runtime copies collide, and the page's `<style>` tag stops taking effect
at all (confirmed: totally unstyled, unreadable page after one save/reload
cycle).

The fix that held: write `HEAD_HTML` and `SAVE_BAR_HTML` as literal
template-literal strings in the script, generated once from the actual
authored markup and never read from the DOM again. The **only** DOM read
that is safe in `buildDocument()` is
`document.getElementById("app-script").outerHTML` — capturing the script's
own tag, which the platform never modifies, so the running script can
re-embed itself verbatim in the next version (the "quine" trick that keeps
the page functional after every save without duplicating the render logic
as a second string).

If you edit the template's CSS or the save-bar markup, the change must land in
**two** places — the literal markup, and the `HEAD_HTML`/`SAVE_BAR_HTML`
constants inside `app-script`. **Do not sync them by hand. Run:**

```bash
python3 skills/decision-queue/scripts/regen_template_constants.py          # rewrite
python3 skills/decision-queue/scripts/regen_template_constants.py --check  # CI mode
```

This section used to say "regenerate with a small script" and ship no script.
What followed was predictable: `SAVE_BAR_HTML` sat as an empty string against
382 characters of real markup, so `buildDocument()` emitted a page with no Save
button, no status line and no counter — savable exactly once, then broken. Four
static checks passed the whole time, because none of them ran `buildDocument()`
and looked at the output. Found and fixed 2026-08-30.

`tests/test_decision_queue_template.py` now fails on any drift, and
`tests/test_decision_queue_render.py` runs the real script under Node and
asserts on what it renders.

## Every interpolation is escaped — keep it that way

The card and header markup is built with the `h` tagged template. It escapes every
`${...}` it interpolates. To insert markup you built yourself, wrap it: `${raw(cells)}`.

```js
h`<h2 class="card-title">${item.title}</h2>`        // escaped — the default
h`<div class="queue">${raw(items.map(cardHtml).join(""))}</div>`  // deliberate HTML
```

This is not style. Item fields come from Operations Center tasks, backlog items, and
peer-authored rally records — text an agent wrote, not text you wrote. Before
2026-08-30 the template called `escapeHtml()` at 5 of ~23 interpolation sites, so every
field added after the first few defaulted to unescaped: `item.title`, `item.decision`,
`item.why`, `item.impact`, `item.recommendation`, `meta.lede`, and `meta.footer` all
reached `innerHTML` raw. Rendering one crafted title through that version produced 14
live `<img onerror>` elements in the reader's page.

`h` inverts the default so the unsafe path is the one you have to type on purpose.
When you add a field to a card, interpolate it and stop — do not reach for `raw()`
unless the value really is markup you constructed.

The same rule covers the data element: `buildDocument()` embeds items through
`safeJsonForScript()`, not bare `JSON.stringify()`, because `JSON.stringify` does not
escape `<` — an item containing `</script>` would close the data element early and the
rest of the document would parse as markup.

`scripts/test_decision_queue_template_escaping.py` renders a hostile item through the
real file and fails if anything executes. It runs in CI. If you restructure the
`<script id="app-script">` block, update that test's extraction with it.

## Other things the template already handles (don't re-solve these)

- **Batched save, not per-keystroke.** Radio/textarea changes update an
  in-memory `items` array; nothing publishes until the Save button fires.
  One `publish()` call per Save click, covering every card at once.
- **Conflict handling.** A `conflict` rejection means someone else (another
  tab, the user themself) already published — the view reloads to the
  winner automatically. No retry, no merge logic needed.
- **Read-only detection.** `not_writer` / `not_granted` / `not_declared` /
  `capability_disabled` all collapse to one read-only state: the Save
  button disables itself and says why, rather than pretending to save.
- **A standing "no longer relevant" response.** `optionsFor()` appends a `×`
  option to every card automatically. Do not author your own — the point is that
  a decision which stopped being a question can be closed without pretending one
  of the real options was chosen.
- **Staleness chip.** A card whose `touched` date is 14+ days old renders an
  "Untouched N days" chip. This requires `opened`/`touched` to be **ISO dates**
  (`2026-08-30`), not prose like "3 weeks ago" — a non-date is ignored, never
  guessed at.
- **Draft persistence.** Selections and comments mirror to `localStorage` on
  every change and clear on a successful publish, so closing the tab mid-queue
  no longer loses typed work. A restored draft never overwrites an answer that
  already round-tripped through publish, and the status line says how many cards
  were restored. Every storage access sits in try/catch — the viewer can throw
  on storage during thumbnail capture or with site data blocked.
- **Filter to unanswered.** A save-bar checkbox hides answered cards through a
  body class. View-only; it never mutates an item.
- **Radio-group semantics.** Options sit in a `fieldset` with a `legend` naming
  the decision, so a screen reader announces each choice with its question
  attached. `#save-status` carries `role="status" aria-live="polite"`.
