"use strict";
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
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.configureLogger = configureLogger;
exports.log = log;
exports.traced = traced;
exports.readLogs = readLogs;
const promises_1 = __importDefault(require("fs/promises"));
const path_1 = __importDefault(require("path"));
const config_1 = require("./config");
const storage_1 = require("./traces/storage");
// ============================================================================
// STATE
// ============================================================================
const LOG_LEVELS = {
    debug: 0,
    info: 1,
    warn: 2,
    error: 3,
};
let config = {
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
function configureLogger(overrides) {
    const paths = (0, config_1.getLogPaths)();
    config = {
        ...config,
        logDir: paths.root,
        ...overrides,
    };
    // Read env var for log level
    const envLevel = process.env.DEBUGGER_LOG_LEVEL;
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
function log(level, op, data) {
    if (LOG_LEVELS[level] < LOG_LEVELS[config.minLevel])
        return;
    const entry = {
        ts: Date.now(),
        level,
        op,
        ...data,
    };
    // Fire and forget — never await, never throw
    appendEntry(entry).catch(() => { });
}
/**
 * Wrap an async operation with automatic start/end/error/duration logging.
 * Returns the function's result unchanged.
 */
async function traced(op, args, fn) {
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
            promoteToTrace(op, dur_ms, args).catch(() => { });
        }
        return result;
    }
    catch (err) {
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
            promoteToTrace(op, dur_ms, args, errorInfo).catch(() => { });
        }
        throw err;
    }
}
/**
 * Read log entries from the debugger's operation log.
 */
async function readLogs(options = {}) {
    const { since, until, level, keyword, limit = 50 } = options;
    const logFile = getOperationsLogPath();
    try {
        const content = await promises_1.default.readFile(logFile, 'utf-8');
        const lines = content.trim().split('\n').filter(Boolean);
        const entries = [];
        for (const line of lines) {
            try {
                const entry = JSON.parse(line);
                // Time filter
                if (since && entry.ts < since)
                    continue;
                if (until && entry.ts > until)
                    continue;
                // Level filter
                if (level && LOG_LEVELS[entry.level] < LOG_LEVELS[level])
                    continue;
                // Keyword filter
                if (keyword) {
                    const kw = keyword.toLowerCase();
                    const searchable = `${entry.op} ${entry.result || ''} ${entry.error?.message || ''}`.toLowerCase();
                    if (!searchable.includes(kw))
                        continue;
                }
                entries.push(entry);
            }
            catch {
                // Skip malformed lines
            }
        }
        // Return most recent entries up to limit
        return entries.slice(-limit);
    }
    catch {
        return [];
    }
}
// ============================================================================
// FILE I/O
// ============================================================================
function getOperationsLogPath() {
    if (!initialized) {
        // Lazy init with defaults
        configureLogger();
    }
    return path_1.default.join(config.logDir, 'operations.jsonl');
}
function getErrorsLogPath() {
    return path_1.default.join(config.logDir, 'errors.jsonl');
}
/**
 * Append a log entry to the JSONL file. Handles rotation.
 */
async function appendEntry(entry) {
    try {
        const logPath = getOperationsLogPath();
        await promises_1.default.mkdir(path_1.default.dirname(logPath), { recursive: true });
        const line = JSON.stringify(entry) + '\n';
        await promises_1.default.appendFile(logPath, line, 'utf-8');
        // Also write errors to separate file for quick access
        if (entry.level === 'error') {
            const errPath = getErrorsLogPath();
            await promises_1.default.appendFile(errPath, line, 'utf-8');
        }
        // Check rotation
        await rotateIfNeeded(logPath);
    }
    catch {
        // Fail silently — broken logging never breaks operations
    }
}
/**
 * Rotate log file if it exceeds maxFileSize.
 */
async function rotateIfNeeded(logPath) {
    try {
        const stat = await promises_1.default.stat(logPath);
        if (stat.size < config.maxFileSize)
            return;
        // Shift existing rotated files
        for (let i = config.maxRotatedFiles - 1; i >= 1; i--) {
            const from = `${logPath}.${i}`;
            const to = `${logPath}.${i + 1}`;
            try {
                await promises_1.default.rename(from, to);
            }
            catch {
                // File doesn't exist, skip
            }
        }
        // Rotate current file
        await promises_1.default.rename(logPath, `${logPath}.1`);
        // Delete oldest if over limit
        try {
            await promises_1.default.unlink(`${logPath}.${config.maxRotatedFiles + 1}`);
        }
        catch {
            // Doesn't exist
        }
    }
    catch {
        // Rotation failed — not critical
    }
}
// ============================================================================
// TRACE PROMOTION
// ============================================================================
/**
 * Promote an error or slow operation to a UnifiedTrace for cross-correlation.
 */
async function promoteToTrace(op, dur_ms, args, error) {
    try {
        const trace = {
            trace_id: (0, storage_1.generateTraceId)(),
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
        await (0, storage_1.storeTrace)(trace);
    }
    catch {
        // Promotion failed — not critical
    }
}
// ============================================================================
// HELPERS
// ============================================================================
/**
 * Sanitize args for logging — remove large values, truncate strings.
 */
function sanitizeArgs(args) {
    if (!args)
        return undefined;
    const sanitized = {};
    for (const [key, value] of Object.entries(args)) {
        if (typeof value === 'string') {
            sanitized[key] = value.length > 200 ? value.slice(0, 200) + '...' : value;
        }
        else if (Array.isArray(value)) {
            sanitized[key] = `[${value.length} items]`;
        }
        else if (typeof value === 'object' && value !== null) {
            sanitized[key] = '{...}';
        }
        else {
            sanitized[key] = value;
        }
    }
    return sanitized;
}
/**
 * Create a brief string summary of a result for the log.
 */
function summarizeResult(result) {
    if (result === undefined || result === null)
        return 'void';
    if (typeof result === 'string')
        return result.slice(0, 100);
    if (typeof result === 'number' || typeof result === 'boolean')
        return String(result);
    if (Array.isArray(result))
        return `[${result.length} items]`;
    // Check for MCP-style response
    if (typeof result === 'object' && result !== null) {
        const obj = result;
        if (obj.content && Array.isArray(obj.content)) {
            const first = obj.content[0];
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
//# sourceMappingURL=logger.js.map