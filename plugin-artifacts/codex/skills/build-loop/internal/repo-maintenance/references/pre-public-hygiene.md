# Pre-Public / Distribution Hygiene

Load this phase when a repo is being prepared for **open-source publication or
paid/external distribution** (any transition from private/solo to external
consumers). It layers on top of the normal maintenance lifecycle: run the
artifact/worktree/branch/local-main phases first, then this.

The governing question shifts from *"is this repo well-structured?"* to *"is it
safe and reproducible in a stranger's hands, and does its history leak anything?"*

Always read [safety-protocol.md](safety-protocol.md) first. History rewrite and
force-push are irreversible and gated on explicit authorization.

## 1. Personal content & secrets in the working tree (SAFE)

Scan tracked files — never the whole checkout (build caches produce noise):

```bash
git grep -nI -e '/Users/' -e '/home/' -e "$(whoami)" \
  -e '[A-Za-z0-9._%+-]\+@[A-Za-z0-9.-]\+\.[A-Za-z]\{2,\}'   # emails
git grep -nIE '(api[_-]?key|secret|token|password|BEGIN (RSA|EC|OPENSSH) PRIVATE KEY|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})'
```

Then **separate leaks from intentional publisher identity** — the second set stays:

| Leave (identity) | Fix (leak) |
|---|---|
| SPDX copyright headers (author name/email) | Absolute home paths (`/Users/<you>/…` → `~/…` or `<repo-root>`) |
| `github.com/<owner>/…` project URLs | Real home paths / usernames in **test fixtures** (violates no-personal-content-in-fixtures) |
| `com.<owner>.*` bundle IDs / reverse-DNS | Personal machine paths in code **comments** / dead references |
| Team ID / signing identity in a *distribution* config | Local `file://…` paths in code defaults → repoint to the public URL |

Code defaults carrying a home path (e.g. a serialized source-pin URL) may be
**behavior-safe to change** — trace whether the value is dereferenced or is pure
metadata, and re-run the test that asserts it. Fix, then re-run the owning test.

## 2. De-track internal artifacts (SAFE — files stay on disk)

`git rm -r --cached` (never delete from disk) + gitignore, for content that
should not ship publicly:

- Tool state: `.navgator/`, `.build-loop/`, `.claude/` scratch, editor/project
  generated dirs — tool artifacts, not source.
- Captured test evidence / scan dumps (screenshots, AX JSON, e2e logs) — usually
  the *bulk* of leaked personal paths; de-track wholesale beats sanitizing each.
- **`archive/*.bundle` / history bundles** — a committed git bundle leaks the
  *entire* pre-sanitization history even after a tree scrub. Always de-track.
- Committed build outputs / vendored binaries → build-from-source instead
  (see stack-profiles.md); a committed binary can drift from or be tampered
  against its source.

## 3. `.git` size: gc BEFORE deciding on a rewrite (SAFE gc / GATED rewrite)

Loose-object bloat is usually reclaimable with **no history change**:

```bash
git count-objects -vH          # if `size` (loose) >> `size-pack`, gc wins big
git gc --prune=2.weeks.ago     # grace window keeps recent dangling objects
```

Only *after* gc, assess whether the remaining pack justifies a history rewrite.
The stronger argument for a rewrite is usually **privacy** (history still holds
every personal path/secret ever committed), not size.

**Rewrite is GATED — never silent.** Prefer the lowest-risk option:

1. **Fresh-cut public repo** (recommended default): publish a new repo whose root
   is a squash/graft at the sanitized commit. Zero rewrite risk to the private
   repo; clean public history. Best when private history need not travel.
2. **`git filter-repo --invert-paths`** on the private repo only when history
   must be preserved public. Before running: `git bundle create ../backup.bundle
   --all` + verify; check whether local `main` is **ahead of `origin`** (a botched
   rewrite with no pushed backup loses unpushed work); every SHA changes and all
   clones/cross-references break. Requires explicit user confirmation.

## 4. Distribution signing / notarization (GATED — needs a credential)

Dev builds commonly ship ad-hoc (`CODE_SIGN_IDENTITY="-"`). A distributed build
needs a real signing identity the maintainer holds — document the runbook, do not
automate cert install (never touch the login keychain automatically):

- Enroll / verify the platform developer program; create the distribution cert
  in the keychain (manual user step).
- Add signing identity + team to a **distribution config variant**, keeping the
  ad-hoc identity as the dev default.
- Sign nested binaries (daemons/helpers) with hardened runtime + timestamp →
  submit to the notary service → staple → verify (`spctl -a -vv` on macOS).

## Execution order & tiers

1. **SAFE, now:** gc → de-track internal artifacts → de-personalize tracked files
   (fixtures/comments/docs/code-default URLs), each code change re-verified by its
   owning test.
2. **GATED:** history-rewrite decision (recommend fresh-cut) — decide only after
   gc; requires confirmation and a backup bundle.
3. **DEFER to credential:** write the signing/notarization runbook now; execute
   when the maintainer has the cert in hand.

Report what was de-tracked (counts), reclaimed size, residual identity strings
intentionally kept, and the gated items awaiting a decision.
