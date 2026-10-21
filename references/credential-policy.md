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
helpers. Prefer connected tools for authenticated writes. The Bash policy refuses `gh` and `claude` authentication-capable commands on
macOS, including token-present invocations: shell text cannot prove that their
effective environment will prevent stored-credential fallback. Help and version
commands remain available. Use connected tools and the host model interface.
Model capacity is not permission to change credentials.

The Bash dispatcher and both system-access wrappers reject recognized Keychain
commands before dispatch. The dispatcher applies this restriction even when
convenience hooks are disabled. The classifier checks command text and Git
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
