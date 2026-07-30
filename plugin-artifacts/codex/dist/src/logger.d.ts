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
export type LogLevel = 'debug' | 'info' | 'warn' | 'error';
export interface LogEntry {
    ts: number;
    level: LogLevel;
    op: string;
    dur_ms?: number;
    args?: Record<string, unknown>;
    result?: string;
    error?: {
        name: string;
        message: string;
        stack?: string;
    };
}
export interface LoggerConfig {
    logDir: string;
    minLevel: LogLevel;
    maxFileSize: number;
    maxRotatedFiles: number;
    promoteErrors: boolean;
    promoteSlowMs: number;
}
/**
 * Configure the logger. Call once at startup.
 */
export declare function configureLogger(overrides?: Partial<LoggerConfig>): void;
/**
 * Write a log entry. Fails silently — broken logging never breaks operations.
 */
export declare function log(level: LogLevel, op: string, data?: Partial<Omit<LogEntry, 'ts' | 'level' | 'op'>>): void;
/**
 * Wrap an async operation with automatic start/end/error/duration logging.
 * Returns the function's result unchanged.
 */
export declare function traced<T>(op: string, args: Record<string, unknown> | undefined, fn: () => Promise<T>): Promise<T>;
export interface ReadLogsOptions {
    since?: number;
    until?: number;
    level?: LogLevel;
    keyword?: string;
    limit?: number;
}
/**
 * Read log entries from the debugger's operation log.
 */
export declare function readLogs(options?: ReadLogsOptions): Promise<LogEntry[]>;
//# sourceMappingURL=logger.d.ts.map