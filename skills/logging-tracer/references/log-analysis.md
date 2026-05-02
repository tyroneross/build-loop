# Log Analysis — Common Patterns & Diagnostic Checklists

## Error Signature Reference

### Connection Errors

| Signature | Likely Cause | Action |
|-----------|-------------|--------|
| `ECONNREFUSED` | Service not running / wrong port | Check if target service is up, verify host:port |
| `ETIMEDOUT` | Network unreachable / firewall | Check network connectivity, DNS resolution |
| `ECONNRESET` | Remote closed connection | Check server logs, look for OOM or crash |
| `EPIPE` | Writing to closed socket | Handle connection lifecycle, add retry logic |
| `ENOTFOUND` | DNS resolution failed | Verify hostname, check DNS config |

### Database Errors

| Signature | Likely Cause | Action |
|-----------|-------------|--------|
| `P2002` (Prisma) | Unique constraint violation | Check for duplicate data, review upsert logic |
| `P2025` (Prisma) | Record not found | Add existence check before update/delete |
| `connection pool exhausted` | Too many concurrent queries | Increase pool size or add connection timeouts |
| `deadlock detected` | Concurrent conflicting transactions | Review transaction isolation, add retry logic |
| `relation does not exist` | Missing migration | Run pending migrations |

### HTTP Errors

| Code | Pattern | Common Cause |
|------|---------|-------------|
| 400 | Repeated from same endpoint | Schema validation failing — check request shape |
| 401 | After period of success | Token expired — check refresh logic |
| 403 | Specific routes only | Permission/role check — verify user permissions |
| 404 | After deployment | Route changed — check routing config |
| 429 | Burst pattern | Rate limit hit — add backoff/queue |
| 500 | Correlates with deploy | Regression — check recent changes |
| 502/503 | Periodic spikes | Upstream health — check dependent services |

### Memory & Performance

| Pattern | Likely Cause | Action |
|---------|-------------|--------|
| Steadily increasing RSS | Memory leak | Profile with --inspect, check for uncleared timers/listeners |
| Periodic CPU spikes | GC pauses or cron jobs | Check GC stats, review scheduled tasks |
| Sudden latency jump | Resource exhaustion | Check file descriptors, connections, thread pool |
| Gradual latency increase | Data growth | Check query plans, add indexes, paginate |

## Diagnostic Checklists

### "Application is slow"

1. Check error logs for timeouts or connection failures
2. Filter for operations with `dur_ms > 2000`
3. Group slow operations by type (db, http, cache)
4. Check if slowness correlates with time of day (traffic) or specific operations
5. Look for N+1 query patterns (many fast DB queries in sequence)
6. Check external API response times
7. Review memory usage trends for GC pressure

### "Intermittent failures"

1. Filter errors by frequency — find the most common error message
2. Check if errors correlate with time patterns (cron, traffic spikes, deployments)
3. Look for resource exhaustion (pool size, rate limits, file descriptors)
4. Check for race conditions (concurrent requests to same resource)
5. Verify retry logic — is it making the problem worse?
6. Check for environmental differences (works in staging, fails in production)

### "No logs at expected point"

1. Verify the code path is reached — add a log before the expected point
2. Check log level configuration — is `LOG_LEVEL` set too high?
3. Verify logger is initialized before first use
4. Check for swallowed exceptions (empty catch blocks)
5. If async: verify await chains are complete, no fire-and-forget promises
6. Check log output destination — stderr vs stdout vs file

### "After deployment regression"

1. Compare error rates before/after deploy timestamp
2. Filter for new error messages (not seen before deploy)
3. Check for environment variable changes
4. Verify database migrations ran successfully
5. Check for dependency version changes
6. Compare request/response shapes if API changed
7. Look for feature flag changes

## Reading JSONL Logs Efficiently

### With the debugger

```
Use the debugger read_logs tool:
- source: "project" to auto-discover project logs
- source: "debugger" for internal operation logs
- since: "1h" for last hour
- level: "error" for errors only
- keyword: "timeout" to search
```

### Command-line quick reference

```bash
# Last 20 errors
grep '"level":"error"' logs/app.jsonl | tail -20 | jq .

# Operations slower than 2 seconds
jq 'select(.dur_ms > 2000)' logs/app.jsonl

# Count errors by operation
jq -r 'select(.level == "error") | .op' logs/app.jsonl | sort | uniq -c | sort -rn

# Errors in last hour (Unix timestamp)
SINCE=$(($(date +%s) * 1000 - 3600000))
jq "select(.ts > $SINCE and .level == \"error\")" logs/app.jsonl

# Time range
jq 'select(.ts > 1710000000000 and .ts < 1710003600000)' logs/app.jsonl
```

## Anti-Patterns

1. **Logging sensitive data** — Never log passwords, tokens, credit card numbers, or PII. Redact before logging.
2. **Console.log in production** — Use structured logging. Console.log has no levels, no timestamps, no structure.
3. **Logging in tight loops** — Log summaries ("processed 150 items") not per-iteration ("processing item 1", "processing item 2"...).
4. **Multiple logging systems** — Pick one logger per service. Don't mix console.log, winston, and pino.
5. **Swallowing errors** — Empty catch blocks hide failures. At minimum, log the error.
6. **Over-logging success paths** — Log entry/exit at debug level, errors at error level. Don't flood info with every successful operation.
