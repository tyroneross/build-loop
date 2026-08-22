# Every build-loop PreToolUse gate is silently inert under Codex

**Status:** open · **Found:** 2026-08-22 · **Verified on:** codex-cli 0.149.0

## What is true today

Codex executes build-loop's plugin hooks. Measured directly by counting hook
events emitted during one `codex exec` turn that used a Bash tool:

```
20 hook: SessionStart      4 hook: UserPromptSubmit
14 hook: Stop              3 hook: PreToolUse      (1 Failed)
                           1 hook: PostToolUse
```

`PreToolUse` fires. The gate behind it never runs.

Every PreToolUse entry in `hooks/hooks.json` resolves its root the same way:

```sh
root="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"
hook="$root/scripts/hooks/pre_bash_dispatch.sh"
if [ -x "$hook" ]; then "$hook"; else printf '{}'; fi
```

Under Codex both variables are empty. Measured inside a Codex session:

```
PLUGIN_ROOT=[]
PROJECT_DIR=[]
ls: /scripts/hooks/pre_bash_dispatch.sh: No such file or directory
```

`root` becomes the empty string, the path becomes `/scripts/hooks/...`, the file
is not executable, and the command prints `{}` — which the hook contract reads as
**allow**. The gate fails open on every command, silently, with no error surfaced
to the user or the agent.

`env | grep -i codex` shows Codex sets no plugin-root variable of any kind
(`CODEX_SESSION_ID`, `CODEX_SANDBOX`, `CODEX_MANAGED_PACKAGE_ROOT`, and others —
none pointing at the installed plugin). There is nothing to fall back to today.

## What that costs

`pre_bash_dispatch.sh` is the single registered PreToolUse entry, and it chains
every Bash-side gate build-loop has:

- `unbounded_wait_gate.py` — duration-waits with no exit condition
- `pre_bash_autonomy.sh` — deploy/push autonomy verdicts (`confirm` / `block`)
- `pre_bash_consent.sh` — CLI dispatch consent
- `pre_bash_dependency_cooldown.sh` — supply-chain publish-age allowlist
- `audit_before_commit.py` — the secrets/conflict hard block (rc==2)
- `security_scan.py` and `deployment_policy.py` on deploy-shaped commands

All of them are unreachable under Codex. A Codex session can today run a
production deploy, install a zero-day-old dependency, or commit a secret, and
build-loop's gates will report nothing. On Claude Code the same command is
gated normally, so the protection looks present in every test run on the
authoring host.

This was found while confirming a much smaller claim — whether an `AGENTS.md`
documentation row was reaching Codex. The row was the visible gap; this was
underneath it.

## Confirming it yourself

```sh
codex exec --model gpt-5.6-luna --sandbox read-only \
  'Run this and paste output verbatim: printf "ROOT=[%s]\n" "$CLAUDE_PLUGIN_ROOT"'
```

An empty `ROOT=[]` reproduces the defect.

## Options, none applied yet

1. **Resolve from `PATH`.** Codex puts `<build-loop>/bin` on `PATH`, so
   `dirname "$(dirname "$(command -v <a-build-loop-bin-entry>)")"` recovers the
   root. Depends on the `bin` entry staying present and first.
2. **Resolve from the repo.** `git rev-parse --show-toplevel` finds the PROJECT
   repo, which is the plugin root only when build-loop is dogfooding itself.
   Wrong for every consumer repo.
3. **Require `BUILD_LOOP_ROOT`.** `AGENTS.md` already documents this variable.
   Explicit and correct, and it does nothing until someone exports it — the same
   activation gap that produced this class of defect.
4. **Ask Codex to expose a plugin-root variable.** Correct long-term, not
   available now.

**A gate that fails open silently is worse than no gate**, because the green run
on Claude Code certifies protection the Codex run does not have. Whatever is
chosen must also emit something visible when the root cannot be resolved, rather
than printing `{}` and continuing.
