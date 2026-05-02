/**
 * Internal Logger & Tracer
 *
 * Structured logging for the debugger's own operations.
 * Zero new dependencies. File-only output (never stdout — MCP uses it).
 *
 * Key exports:
 * - traced(op, args, fn) — wrap any async operation with start/end/error/duration
 * - log(level, op, data) — direct log call
 * - readLogs(options) — read log entries with filtering
 * - configureLogger(overrides) — set log dir, min level, etc.
 */

import fs from 'fs/promises';
import path from 'path';
import { getLogPaths } from './config';
import { storeTrace, generateTraceId } from './traces/storage';
import type { UnifiedTrace } from './traces/types';

// ============================================================================
// TYPES
// ============================================================================

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEntry {
  ts: number;
  level: LogLevel;
  op: string;
  dur_ms?: number;
  args?: Record<string, unknown>;
  result?: string;
  error?: { name: string; message: string; stack?: string };
}

export interface LoggerConfig {
  logDir: string;
  minLevel: LogLevel;
  maxFileSize: number;       // bytes, rotation threshold
  maxRotatedFiles: number;
  promoteErrors: boolean;    // promote errors to UnifiedTrace
  promoteSlowMs: number;     // promote operations slower than this
}

// ============================================================================
// STATE
// ============================================================================

const LOG_LEVELS: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

let config: LoggerConfig = {
  logDir: '',
  minLevel: 'info',
  maxFileSize: 10 * 1024 * 1024, // 10MB
  maxRotatedFiles: 2,
  promoteErrors: true,
  promoteSlowMs: 2000,
};

let initialized = false;

// ============================================================================
// CONFIGURATION
// ============================================================================

/**
 * Configure the logger. Call once at startup.
 */
export function configureLogger(overrides?: Partial<LoggerConfig>): void {
  const paths = getLogPaths();
  config = {
    ...config,
    logDir: paths.root,
    ...overrides,
  };

  // Read env var for log level
  const envLevel = process.env.DEBUGGER_LOG_LEVEL as LogLevel | undefined;
  if (envLevel && envLevel in LOG_LEVELS) {
    config.minLevel = envLevel;
  }

  initialized = true;
}

// ============================================================================
// CORE LOGGING
// ============================================================================

/**
 * Write a log entry. Fails silently — broken logging never breaks operations.
 */
export function log(level: LogLevel, op: string, data?: Partial<Omit<LogEntry, 'ts' | 'level' | 'op'>>): void {
  if (LOG_LEVELS[level] < LOG_LEVELS[config.minLevel]) return;

  const entry: LogEntry = {
    ts: Date.now(),
    level,
    op,
    ...data,
  };

  // Fire and forget — never await, never throw
  appendEntry(entry).catch(() => {});
}

/**
 * Wrap an async operation with automatic start/end/error/duration logging.
 * Returns the function's result unchanged.
 */
export async function traced<T>(
  op: string,
  args: Record<string, unknown> | undefined,
  fn: () => Promise<T>
): Promise<T> {
  const start = Date.now();

  try {
    const result = await fn();
    const dur_ms = Date.now() - start;

    log('info', op, {
      dur_ms,
      args: sanitizeArgs(args),
      result: summarizeResult(result),
    });

    // Promote slow operations
    if (config.promoteErrors && dur_ms > config.promoteSlowMs) {
      promoteToTrace(op, dur_ms, args).catch(() => {});
    }

    return result;
  } catch (err) {
    const dur_ms = Date.now() - start;
    const errorInfo = err instanceof Error
      ? { name: err.name, message: err.message, stack: err.stack?.split('\n').slice(0, 4).join('\n') }
      : { name: 'Unknown', message: String(err) };

    log('error', op, {
      dur_ms,
      args: sanitizeArgs(args),
      error: errorInfo,
    });

    // Promote errors
    if (config.promoteErrors) {
      promoteToTrace(op, dur_ms, args, errorInfo).catch(() => {});
    }

    throw err;
  }
}

// ============================================================================
// LOG READING
// ============================================================================

export interface ReadLogsOptions {
  since?: number;       // Unix timestamp
  until?: number;       // Unix timestamp
  level?: LogLevel;     // Minimum level
  keyword?: string;     // Search in op and result
  limit?: number;       // Max entries to return (default 50)
}

/**
 * Read log entries from the debugger's operation log.
 */
export async function readLogs(options: ReadLogsOptions = {}): Promise<LogEntry[]> {
  const { since, until, level, keyword, limit = 50 } = options;
  const logFile = getOperationsLogPath();

  try {
    const content = await fs.readFile(logFile, 'utf-8');
    const lines = content.trim().split('\n').filter(Boolean);

    const entries: LogEntry[] = [];

    for (const line of lines) {
      try {
        const entry: LogEntry = JSON.parse(line);

        // Time filter
        if (since && entry.ts < since) continue;
        if (until && entry.ts > until) continue;

        // Level filter
        if (level && LOG_LEVELS[entry.level] < LOG_LEVELS[level]) continue;

        // Keyword filter
        if (keyword) {
          const kw = keyword.toLowerCase();
          const searchable = `${entry.op} ${entry.result || ''} ${entry.error?.message || ''}`.toLowerCase();
          if (!searchable.includes(kw)) continue;
        }

        entries.push(entry);
      } catch {
        // Skip malformed lines
      }
    }

    // Return most recent entries up to limit
    return entries.slice(-limit);
  } catch {
    return [];
  }
}

// ============================================================================
// FILE I/O
// ============================================================================

function getOperationsLogPath(): string {
  if (!initialized) {
    // Lazy init with defaults
    configureLogger();
  }
  return path.join(config.logDir, 'operations.jsonl');
}

function getErrorsLogPath(): string {
  return path.join(config.logDir, 'errors.jsonl');
}

/**
 * Append a log entry to the JSONL file. Handles rotation.
 */
async function appendEntry(entry: LogEntry): Promise<void> {
  try {
    const logPath = getOperationsLogPath();
    await fs.mkdir(path.dirname(logPath), { recursive: true });

    const line = JSON.stringify(entry) + '\n';
    await fs.appendFile(logPath, line, 'utf-8');

    // Also write errors to separate file for quick access
    if (entry.level === 'error') {
      const errPath = getErrorsLogPath();
      await fs.appendFile(errPath, line, 'utf-8');
    }

    // Check rotation
    await rotateIfNeeded(logPath);
  } catch {
    // Fail silently — broken logging never breaks operations
  }
}

/**
 * Rotate log file if it exceeds maxFileSize.
 */
async function rotateIfNeeded(logPath: string): Promise<void> {
  try {
    const stat = await fs.stat(logPath);
    if (stat.size < config.maxFileSize) return;

    // Shift existing rotated files
    for (let i = config.maxRotatedFiles - 1; i >= 1; i--) {
      const from = `${logPath}.${i}`;
      const to = `${logPath}.${i + 1}`;
      try {
        await fs.rename(from, to);
      } catch {
        // File doesn't exist, skip
      }
    }

    // Rotate current file
    await fs.rename(logPath, `${logPath}.1`);

    // Delete oldest if over limit
    try {
      await fs.unlink(`${logPath}.${config.maxRotatedFiles + 1}`);
    } catch {
      // Doesn't exist
    }
  } catch {
    // Rotation failed — not critical
  }
}

// ============================================================================
// TRACE PROMOTION
// ============================================================================

/**
 * Promote an error or slow operation to a UnifiedTrace for cross-correlation.
 */
async function promoteToTrace(
  op: string,
  dur_ms: number,
  args?: Record<string, unknown>,
  error?: { name: string; message: string; stack?: string }
): Promise<void> {
  try {
    const trace: UnifiedTrace = {
      trace_id: generateTraceId(),
      source: 'internal',
      timestamp: Date.now(),
      duration_ms: dur_ms,
      severity: error ? 'error' : 'warning',
      category: 'custom',
      operation: op,
      summary: {
        description: error
          ? `${op} failed: ${error.message}`.slice(0, 200)
          : `${op} slow: ${dur_ms}ms`.slice(0, 200),
        highlights: [
          { label: 'operation', value: op },
          { label: 'duration', value: `${dur_ms}ms` },
          ...(args ? [{ label: 'args', value: JSON.stringify(args).slice(0, 100) }] : []),
        ],
        ...(error ? {
          error: {
            type: error.name,
            message: error.message.slice(0, 200),
            stack_preview: error.stack,
          },
        } : {}),
        performance: {
          duration_ms: dur_ms,
          is_slow: dur_ms > config.promoteSlowMs,
        },
      },
      has_full_data: false,
      tokens_estimated: 50,
    };

    await storeTrace(trace);
  } catch {
    // Promotion failed — not critical
  }
}

// ============================================================================
// HELPERS
// ============================================================================

/**
 * Sanitize args for logging — remove large values, truncate strings.
 */
function sanitizeArgs(args?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!args) return undefined;

  const sanitized: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(args)) {
    if (typeof value === 'string') {
      sanitized[key] = value.length > 200 ? value.slice(0, 200) + '...' : value;
    } else if (Array.isArray(value)) {
      sanitized[key] = `[${value.length} items]`;
    } else if (typeof value === 'object' && value !== null) {
      sanitized[key] = '{...}';
    } else {
      sanitized[key] = value;
    }
  }
  return sanitized;
}

/**
 * Create a brief string summary of a result for the log.
 */
function summarizeResult(result: unknown): string {
  if (result === undefined || result === null) return 'void';
  if (typeof result === 'string') return result.slice(0, 100);
  if (typeof result === 'number' || typeof result === 'boolean') return String(result);
  if (Array.isArray(result)) return `[${result.length} items]`;

  // Check for MCP-style response
  if (typeof result === 'object' && result !== null) {
    const obj = result as Record<string, unknown>;
    if (obj.content && Array.isArray(obj.content)) {
      const first = obj.content[0] as Record<string, unknown> | undefined;
      if (first?.text && typeof first.text === 'string') {
        return first.text.slice(0, 100);
      }
    }
    // Generic object
    const keys = Object.keys(obj);
    return `{${keys.slice(0, 3).join(', ')}${keys.length > 3 ? '...' : ''}}`;
  }

  return typeof result;
}
