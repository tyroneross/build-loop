# Stack Templates — Logging & Tracing

## Node.js / TypeScript (Tier 1)

```typescript
// lib/logger.ts
type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVELS: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const MIN_LEVEL: LogLevel = (process.env.LOG_LEVEL as LogLevel) || 'info';

function log(level: LogLevel, op: string, data?: Record<string, unknown>) {
  if (LEVELS[level] < LEVELS[MIN_LEVEL]) return;
  const entry = { ts: Date.now(), level, op, ...data };
  process.stderr.write(JSON.stringify(entry) + '\n');
}

async function traced<T>(op: string, fn: () => Promise<T>, ctx?: Record<string, unknown>): Promise<T> {
  const start = Date.now();
  try {
    const result = await fn();
    log('info', op, { dur_ms: Date.now() - start, ...ctx });
    return result;
  } catch (err) {
    const e = err instanceof Error ? err : new Error(String(err));
    log('error', op, { dur_ms: Date.now() - start, error: { name: e.name, message: e.message }, ...ctx });
    throw err;
  }
}

export { log, traced, LogLevel };
```

### Usage

```typescript
import { log, traced } from './lib/logger';

// Direct log
log('info', 'server:start', { port: 3000 });

// Wrap async operation
const user = await traced('db:getUser', () => db.user.findUnique({ where: { id } }), { userId: id });

// Error handling
try {
  await riskyOperation();
} catch (err) {
  log('error', 'payment:charge', {
    error: { name: err.name, message: err.message },
    customerId,
  });
}
```

## Node.js / TypeScript (Tier 2 — File Output)

```typescript
// lib/logger.ts
import { appendFileSync, statSync, renameSync, mkdirSync } from 'fs';
import { join } from 'path';

type LogLevel = 'debug' | 'info' | 'warn' | 'error';
const LEVELS: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const MIN_LEVEL: LogLevel = (process.env.LOG_LEVEL as LogLevel) || 'info';

const LOG_DIR = join(process.cwd(), 'logs');
const LOG_FILE = join(LOG_DIR, 'app.jsonl');
const MAX_SIZE = 10 * 1024 * 1024; // 10MB
const MAX_ROTATED = 2;

try { mkdirSync(LOG_DIR, { recursive: true }); } catch {}

function write(entry: Record<string, unknown>) {
  try {
    appendFileSync(LOG_FILE, JSON.stringify(entry) + '\n');
    rotate();
  } catch {}
}

function rotate() {
  try {
    const stat = statSync(LOG_FILE);
    if (stat.size < MAX_SIZE) return;
    for (let i = MAX_ROTATED; i >= 1; i--) {
      try { renameSync(`${LOG_FILE}.${i}`, `${LOG_FILE}.${i + 1}`); } catch {}
    }
    renameSync(LOG_FILE, `${LOG_FILE}.1`);
  } catch {}
}

function log(level: LogLevel, op: string, data?: Record<string, unknown>) {
  if (LEVELS[level] < LEVELS[MIN_LEVEL]) return;
  write({ ts: Date.now(), level, op, ...data });
}

async function traced<T>(op: string, fn: () => Promise<T>, ctx?: Record<string, unknown>): Promise<T> {
  const start = Date.now();
  try {
    const result = await fn();
    log('info', op, { dur_ms: Date.now() - start, ...ctx });
    return result;
  } catch (err) {
    const e = err instanceof Error ? err : new Error(String(err));
    log('error', op, { dur_ms: Date.now() - start, error: { name: e.name, message: e.message }, ...ctx });
    throw err;
  }
}

export { log, traced };
```

## Python (Tier 1)

```python
# lib/logger.py
import json, time, sys, os, functools
from typing import Any

LEVELS = {'debug': 0, 'info': 1, 'warn': 2, 'error': 3}
MIN_LEVEL = os.environ.get('LOG_LEVEL', 'info')

def log(level: str, op: str, **data: Any):
    if LEVELS.get(level, 0) < LEVELS.get(MIN_LEVEL, 1):
        return
    entry = {'ts': int(time.time() * 1000), 'level': level, 'op': op, **data}
    print(json.dumps(entry), file=sys.stderr)

def traced(op: str, **ctx):
    def decorator(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await fn(*args, **kwargs)
                log('info', op, dur_ms=int((time.time() - start) * 1000), **ctx)
                return result
            except Exception as e:
                log('error', op, dur_ms=int((time.time() - start) * 1000),
                    error={'name': type(e).__name__, 'message': str(e)}, **ctx)
                raise

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = fn(*args, **kwargs)
                log('info', op, dur_ms=int((time.time() - start) * 1000), **ctx)
                return result
            except Exception as e:
                log('error', op, dur_ms=int((time.time() - start) * 1000),
                    error={'name': type(e).__name__, 'message': str(e)}, **ctx)
                raise

        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper
    return decorator
```

### Usage

```python
from lib.logger import log, traced

log('info', 'server:start', port=8000)

@traced('db:get_user')
async def get_user(user_id: str):
    return await db.users.find_one({"_id": user_id})
```

## Go (Tier 1)

```go
// pkg/logger/logger.go
package logger

import (
    "encoding/json"
    "fmt"
    "os"
    "strings"
    "time"
)

var levels = map[string]int{"debug": 0, "info": 1, "warn": 2, "error": 3}
var minLevel = getEnvOr("LOG_LEVEL", "info")

type Entry struct {
    Ts    int64                  `json:"ts"`
    Level string                 `json:"level"`
    Op    string                 `json:"op"`
    DurMs *int64                 `json:"dur_ms,omitempty"`
    Data  map[string]interface{} `json:"data,omitempty"`
}

func Log(level, op string, data map[string]interface{}) {
    if levels[level] < levels[minLevel] { return }
    entry := Entry{Ts: time.Now().UnixMilli(), Level: level, Op: op, Data: data}
    b, _ := json.Marshal(entry)
    fmt.Fprintln(os.Stderr, string(b))
}

func Traced(op string, fn func() error) error {
    start := time.Now()
    if err := fn(); err != nil {
        dur := time.Since(start).Milliseconds()
        Log("error", op, map[string]interface{}{"dur_ms": dur, "error": err.Error()})
        return err
    }
    dur := time.Since(start).Milliseconds()
    Log("info", op, map[string]interface{}{"dur_ms": dur})
    return nil
}

func getEnvOr(key, fallback string) string {
    if v := os.Getenv(key); v != "" { return strings.ToLower(v) }
    return fallback
}
```

## Node.js (Tier 3 — OpenTelemetry)

```typescript
// lib/tracing.ts
import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';
import { Resource } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME } from '@opentelemetry/semantic-conventions';
import { TraceIdRatioBasedSampler, ParentBasedSampler } from '@opentelemetry/sdk-trace-base';

// Prevent duplicate init in hot-reload
const INIT_KEY = Symbol.for('otel.initialized');
if ((globalThis as any)[INIT_KEY]) {
  console.warn('[tracing] Already initialized, skipping');
} else {
  const isDev = process.env.NODE_ENV !== 'production';

  const sdk = new NodeSDK({
    resource: new Resource({ [ATTR_SERVICE_NAME]: process.env.SERVICE_NAME || 'my-app' }),
    traceExporter: new OTLPTraceExporter({
      url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://localhost:4318/v1/traces',
    }),
    sampler: new ParentBasedSampler({
      root: new TraceIdRatioBasedSampler(isDev ? 1.0 : 0.1),
    }),
    instrumentations: [getNodeAutoInstrumentations()],
  });

  sdk.start();
  (globalThis as any)[INIT_KEY] = true;

  process.on('SIGTERM', () => sdk.shutdown());
}
```

### Required packages
```
@opentelemetry/sdk-node
@opentelemetry/exporter-trace-otlp-http
@opentelemetry/auto-instrumentations-node
@opentelemetry/resources
@opentelemetry/semantic-conventions
```

### Free backends
- **Jaeger** (local): `docker run -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one`
- **SigNoz** (self-hosted): Follow docs at signoz.io
- **Grafana Tempo** (self-hosted): Pair with Grafana for UI
