---
name: feedback
description: Report a bug or send feedback about the build-loop plugin
---

# Report build-loop feedback

File the user's report as a GitHub issue on `tyroneross/build-loop`. Issues are this plugin's
support channel — the manifest carries no contact address by design.

## Steps

1. Ask what went wrong, if the user has not already said. One question, not a form.
2. Gather the context that makes a report actionable, without interrogating the user:
   - plugin version from `.claude-plugin/plugin.json`
   - `claude --version`
   - `uname -sm`
   - which command or skill misbehaved, and what it did instead
3. Show the user the exact title and body you intend to file. Their report, their words.
4. Create it:

```bash
gh issue create --repo tyroneross/build-loop \
  --title "<one line: what broke>" \
  --body "<what happened / what was expected / steps / versions>"
```

5. If `gh` is missing or unauthenticated, do not fail — print the URL so the user
   can open it in a browser: https://github.com/tyroneross/build-loop/issues/new

6. Report the resulting issue URL back to the user.

## Rules

- A GitHub issue is public. Redact secrets, tokens, absolute home paths, and any
  file contents the user has not seen before sending.
- Never file without showing the user the body first.
