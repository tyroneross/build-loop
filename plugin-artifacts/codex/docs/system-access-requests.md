# System access requests

Use `scripts/system_access_request.py` for a read-only command that may cause a macOS system-access or administrator request. It prints the command's purpose and scope before dispatching it, records that one request, and makes an identical caller wait for its result.

```bash
python3 scripts/system_access_request.py \
  --purpose "Inspect macOS background task registrations" \
  --scope "Background task registration metadata" \
  --requester "codex:<task-id>" \
  -- sfltool dumpbtm
```

The wrapper never reads, stores, or reuses a password. It never retries a request after it has been dispatched, denied, cancelled, or failed. It deduplicates that result for five minutes by default; a later caller is a new request. A failure to start before macOS is reached is immediately retryable.

Codex hooks can display advisory guidance but cannot reliably prevent a direct shell command from launching. Use the wrapper for the exact-once guarantee.
