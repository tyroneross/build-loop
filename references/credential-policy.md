# Credential access

Build Loop must never probe Apple Keychain for stored credentials. Do not read,
write, migrate, unlock, reset, or retry Keychain authentication. Preserve stored
credentials. A general request to continue, a broker request, or a prior retry
receipt does not authorize Keychain access.

Use an already authorized service connector or the designated Secrets Vault.
Use `vault status` and `vault list --search NAME --json` for metadata. Use the
vault's server-side injection interface for an approved project destination.
Never print decrypted values, use `vault get --value`, inspect vault session
files, or replace Keychain with plaintext secret storage. If the vault is locked
or unavailable, leave the dependent feature unavailable and continue independent
work. Do not automatically unlock it or trigger authentication prompts.

On macOS, Git network commands must not invoke `osxkeychain` or the plaintext
`store` helper. Public, anonymous reads can explicitly disable credential
helpers. Prefer connected tools when they support the requested operation.
Owner update, 2026-11-11: normal `gh api`, `release`, `run`, `workflow`, `search`,
`label`, `status`, `browse`, `repo view/list`, `pr view/list/diff/checks` and
`issue view/list` commands may use the
existing configured login, including incidental Keychain access. This supersedes
the earlier blanket gh restriction. The requested operation still needs its own
authorization. Direct credential extraction or changes (`gh auth`), extensions,
and unknown aliases remain refused; help/version remain available. Git-launching
commands such as `gh repo clone` and `gh pr checkout` remain refused; use the
existing inspected Git transport route. GH_BROWSER/GH_PAGER/GH_EDITOR commands
are checked by the credential classifier too. Never extract
tokens or use plaintext storage. If login or unlock is requested, stop that path;
retry only after relevant state or explicit authorization changes.
Model capacity is not permission to change credentials.

Owner update, 2026-11-08. Claude commands may use the configured login. The
credential policy permits ordinary Claude invocations and their literal command
wrappers. Normal login credential-helper use needs no separate approval.

Claude Code registers the Bash dispatcher, which rejects recognized Keychain
commands before dispatch even when convenience hooks are disabled. Codex ships
no automatic Build Loop hooks: agents must invoke the credential-policy checker
or dispatcher explicitly before credential-capable commands and honor deny or
unavailable results. This is a required agent protocol, not automatic Codex tool
interception. Both system-access wrappers enforce the same restriction when
invoked. Legacy Codex envelope handling in the dispatcher remains callable;
it does not establish automatic registration or host-level enforcement. The classifier checks command text and Git
helper configuration, including scoped helpers. Dynamic inline Git environment
overrides and shell directory changes require an inspectable configuration path.
Use Git's explicit `-C` option instead of `cd` or `env -C` for network commands,
so the metadata check inspects the repository Git will use. Opaque interpreter API references are inspected conservatively; single-quoted
shell examples remain inert.
The checker never invokes credential helpers or reads secret
values. Opaque scripts, applications, and SDK calls remain subject to this same
agent instruction; this hook is not an operating-system sandbox. Test rejection
with synthetic command strings and fake runners, never production probes.

When a model reaches capacity, try Astra (`gpt-6-astra`), then Luna
(`gpt-6-luna`) through an approved host. Record the actual model and vendor.
A same-vendor fallback does not satisfy an independent cross-vendor review.
