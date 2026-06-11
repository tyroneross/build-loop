# Archive

Snapshots of build-loop main kept for revert safety. Not part of the functional plugin
(no commands/agents/skills here; `export-ignore` + `.npmignore`).

| Tag | SHA | What it predates | Revert |
|-----|-----|------------------|--------|
| `archive/pre-trio-merge-20260610` | 3e50f44 | the Advisor/ledger/dispatch-ladder trio merge | `git reset --hard archive/pre-trio-merge-20260610` |

Bundles (`*.bundle`) are portable full-history copies (`git clone <bundle>`); the tag is the
primary revert path.
