# Intent Capability Pack

Use this pack on every build. It keeps decentralized subagent work aligned to the app's purpose, the user's actual job, and the update's intent.

## North Star

Every build starts by capturing:

- **App/repo purpose**: what this product is for and who it serves.
- **Primary users**: the people or roles affected by this change.
- **Core jobs**: the tasks users perform most often or rely on most.
- **Update intent**: why this change matters now.
- **User value**: how the change makes the product faster, clearer, more accurate, more trustworthy, more useful, or easier to navigate.
- **Non-goals**: what this build should not add, expose, or complicate.

Write the result to `.build-loop/intent.md` and mirror the compact version into `.build-loop/state.json.intent`.

## Intent Packet

Every subagent prompt must include this packet:

```md
North star: <one sentence>
Update intent: <one sentence>
Primary user/workflow: <who does what>
This task fits by: <how this subtask advances the build>
User-value rule: <speed | accuracy | trust | navigation | scalability | reduced choice burden | other>
Decision constraints:
- No fake data or mock responses in production/user decision paths.
- No dead controls, dead navigation, decorative options, or UI promises without working behavior.
- Prefer the simplest approach that preserves user value and long-term scalability.
- Use a more complex approach only when the simpler approach harms user experience, correctness, extensibility, or performance.
Evidence required: <tests, build, visual check, data trace, performance check, etc.>
```

## Decision Rules

- **Real value beats apparent progress**. A UI that looks complete but hides mock data is worse than an honest incomplete state.
- **Basics must be excellent**. Core flows, data accuracy, loading, empty states, error states, navigation, and primary actions matter more than secondary features.
- **Every visible element needs intent**. Each button, label, option, nav item, chart, and message must help the user act, understand, decide, or recover.
- **One clear primary action by default**. Multiple hero or primary buttons need a strong reason. If choices create confusion, reduce them.
- **No non-working promises**. Do not ship listed options, nav items, filters, actions, charts, or integrations that do nothing or return placeholders.
- **Simplicity is not shortcutting**. Prefer the smallest durable solution. Choose additional complexity only when it materially improves user value, reliability, scalability, or future optionality.
- **End-to-end data integrity matters**. If users make decisions from search, charts, metrics, recommendations, or summaries, trace those outputs to real sources.

## UI Standard: Beauty in the Basics

For UI work, the baseline is intentional, useful, and polished:

- **Hierarchy**: the screen makes the next best action obvious.
- **Copy**: text is specific, truthful, and necessary. Remove generic filler.
- **Controls**: controls have working behavior, appropriate affordance, and accessible labels.
- **Navigation**: navigation reflects real destinations and common workflows.
- **Choices**: option count is constrained to what users can meaningfully use.
- **States**: loading, empty, error, success, disabled, and permission states are designed, not incidental.
- **Data displays**: charts, tables, search results, and metrics show real data or a clear unavailable state.
- **Performance**: avoid visual or data-flow choices that make common tasks slower without clear value.
- **Scalability**: layouts and data models should tolerate realistic growth without immediate redesign.

## User-Impact Issue Rule

When build-loop discovers a bug or issue while working:

1. Ask whether it impacts users by checking:
   - Does it make the app slower or faster?
   - Does it make information less or more accurate?
   - Does it affect trust, data integrity, security, or recovery from failure?
   - Does it make core workflows easier or harder to navigate?
   - Does it add unnecessary choices or remove useful optionality?
   - Does it create short-term code that blocks scalable future work?
2. If yes and the fix is local to the current build, add it to the plan and fix it automatically.
3. If yes but the fix is too large or risky, log it to `.build-loop/issues/` with user impact, proposed fix, and why it was deferred.
4. If no, log only when it is likely to affect future maintenance.

## Review Gates

Review must check:

- **Intent fidelity**: the implementation advances the north star and update intent.
- **User value**: the result improves at least one declared user-value rule.
- **UI intentionality**: visible elements are meaningful, working, and not excessive.
- **Data integrity**: production/user decision paths do not use fake, random, or placeholder data.
- **Simplicity and scalability**: the solution is the simplest durable approach that protects user experience.

## Source Basis

This pack operationalizes human-centered design and usability principles from:

- [ISO 9241-210:2019](https://www.iso.org/standard/77520.html): human-centered design across the interactive-system life cycle.
- [NIST summary of ISO human-centered design](https://www.nist.gov/itl/iad/visualization-and-usability-group/human-factors-human-centered-design): explicit users/tasks/environments, iterative evaluation, whole user experience, and multidisciplinary perspective.
- [GOV.UK Service Manual: understand users and their needs](https://www.gov.uk/service-manual/service-standard/point-1-understand-user-needs): understand full context, validate assumptions, and avoid building the wrong thing.
- [GOV.UK Service Manual: learning about users and their needs](https://www.gov.uk/service-manual/user-centred-design/user-needs): design around real user needs and keep needs traceable to user stories.
- [W3C WCAG 2.2 Understanding](https://www.w3.org/WAI/WCAG22/understanding/): accessible interfaces should be perceivable, operable, understandable, and robust.
- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/): hierarchy, harmony, consistency, accessibility, platform patterns, and common components.
- [Nielsen Norman Group usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/): visibility, match to real world, user control, consistency, error prevention, recognition, flexibility, minimalist design, recovery, and help.
