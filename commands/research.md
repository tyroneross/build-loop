---
name: research
description: "Generate a repo-grounded research packet without committing to build. Pre-decision analysis."
argument-hint: "[topic]"
---

Load the `build-loop:research` skill.

{{#if ARGUMENTS}}
Topic: `{{ARGUMENTS}}`

Run the research packet workflow:
1. Scan the repo for context relevant to the topic
2. Classify the task type
3. Generate a structured research packet (Bottom line, What I found, Best path, Why, Risks, Confidence, Next action)
4. Archive to `.build-loop/research/`
5. Present the packet — user decides next step: build, optimize, or shelve
{{else}}
No topic specified. Ask the user what they want to research or evaluate.
{{/if}}
