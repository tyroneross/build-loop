"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Log Reader
 *
 * Powers the `read_logs` MCP tool. Reads logs from:
 * 1. Build-loop debugging operation logs (.build-loop/logs/debugging/)
 * 2. Project logs — auto-discovers common log locations
 * 3. Explicit file paths
 *
 * Supports JSONL (structured) and plain text (regex-based extraction).
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.readProjectLogs = readProjectLogs;
const promises_1 = __importDefault(require("fs/promises"));
const fs_1 = require("fs");
const path_1 = __importDefault(require("path"));
const config_1 = require("./config");
// ============================================================================
// MAIN READER
// ============================================================================
/**
 * Read logs from the specified source.
 */
async function readProjectLogs(options) {
    const limit = options.limit ?? 50;
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000)
        throw new Error('limit must be an integer from 1 to 1000');
    const sinceTs = options.since ? parseRelativeTime(options.since) : undefined;
    const untilTs = options.until ? parseRelativeTime(options.until) : undefined;
    const minLevel = options.level ? SEVERITY_ORDER[options.level] : 0;
    const keyword = options.keyword?.toLowerCase();
    let files = [];
    switch (options.source) {
        case 'debugger':
            files = await getDebuggerLogFiles();
            break;
        case 'project':
            files = await discoverProjectLogFiles();
            break;
        case 'path':
            if (!options.path)
                throw new Error('path is required when source is "path"');
            files = [options.path];
            break;
    }
    const selected = [];
    const filesRead = [];
    let totalMatched = 0;
    const coverage = { complete: true, bytes_read: 0, max_bytes: 64 * 1024 * 1024,
        skipped_files: [], malformed_lines: 0 };
    for (const file of [...new Set(files)]) {
        try {
            let jsonl;
            for await (const line of readLogLines(file, coverage)) {
                if (!line.trim())
                    continue;
                if (jsonl === undefined) {
                    try {
                        JSON.parse(line);
                        jsonl = true;
                    }
                    catch {
                        jsonl = false;
                    }
                }
                let entries;
                try {
                    entries = jsonl ? parseJsonlLines([line], file) : parseTextLines([line], file);
                }
                catch {
                    entries = [];
                }
                if (!entries.length) {
                    coverage.malformed_lines++;
                    coverage.complete = false;
                }
                for (const entry of entries) {
                    const timestamp = new Date(entry.timestamp).getTime();
                    if (sinceTs !== undefined && timestamp < sinceTs || untilTs !== undefined && timestamp > untilTs ||
                        SEVERITY_ORDER[entry.level] < minLevel || keyword && !entry.message.toLowerCase().includes(keyword))
                        continue;
                    totalMatched++;
                    let lo = 0, hi = selected.length;
                    while (lo < hi) {
                        const mid = (lo + hi) >>> 1;
                        if (new Date(selected[mid].timestamp).getTime() >= timestamp)
                            lo = mid + 1;
                        else
                            hi = mid;
                    }
                    if (lo < limit) {
                        selected.splice(lo, 0, entry);
                        if (selected.length > limit)
                            selected.pop();
                    }
                }
            }
            filesRead.push(file);
        }
        catch (error) {
            coverage.complete = false;
            coverage.skipped_files.push({ file, reason: error instanceof Error ? error.message : String(error) });
        }
    }
    return { entries: selected, files_read: filesRead, total_matched: totalMatched,
        truncated: totalMatched > limit || !coverage.complete, coverage };
}
// ============================================================================
// FILE DISCOVERY
// ============================================================================
const SEVERITY_ORDER = {
    debug: 0, info: 1, warn: 2, warning: 2, error: 3, fatal: 4,
};
/**
 * Get debugger's own log files.
 */
async function getDebuggerLogFiles() {
    const paths = (0, config_1.getLogPaths)();
    const files = [];
    for (const logFile of [paths.operations, paths.errors]) {
        try {
            await promises_1.default.access(logFile);
            files.push(logFile);
        }
        catch {
            // Doesn't exist yet
        }
    }
    return files;
}
/**
 * Auto-discover project log files in common locations.
 */
async function discoverProjectLogFiles() {
    const cwd = process.cwd();
    const discovered = [];
    // Common log directories
    const logDirs = ['logs', 'log', '.logs'];
    for (const dir of logDirs) {
        const dirPath = path_1.default.join(cwd, dir);
        try {
            const entries = await promises_1.default.readdir(dirPath);
            for (const entry of entries) {
                if (entry.endsWith('.log') || entry.endsWith('.jsonl')) {
                    discovered.push(path_1.default.join(dirPath, entry));
                }
            }
        }
        catch {
            // Dir doesn't exist
        }
    }
    // Common log files in project root
    const rootLogs = [
        'npm-debug.log',
        'yarn-error.log',
        'pnpm-debug.log',
        'lerna-debug.log',
    ];
    for (const logFile of rootLogs) {
        const filePath = path_1.default.join(cwd, logFile);
        try {
            await promises_1.default.access(filePath);
            discovered.push(filePath);
        }
        catch {
            // Doesn't exist
        }
    }
    // Next.js trace
    const nextTrace = path_1.default.join(cwd, '.next', 'trace');
    try {
        await promises_1.default.access(nextTrace);
        discovered.push(nextTrace);
    }
    catch {
        // Not a Next.js project or trace not enabled
    }
    // Also check debugger's own logs
    const debuggerFiles = await getDebuggerLogFiles();
    discovered.push(...debuggerFiles);
    return discovered;
}
// ============================================================================
// FILE PARSING
// ============================================================================
/**
 * Read and parse a log file. Auto-detects format.
 */
async function* readLogLines(filePath, coverage) {
    const handle = await promises_1.default.open(filePath, fs_1.constants.O_RDONLY | fs_1.constants.O_NONBLOCK);
    try {
        if (!(await handle.stat()).isFile())
            throw new Error('Log source must be a regular file');
        let pending = Buffer.alloc(0);
        while (true) {
            const remaining = coverage.max_bytes - coverage.bytes_read;
            if (remaining <= 0)
                throw new Error('Log scan reached aggregate byte budget');
            const block = Buffer.allocUnsafe(Math.min(64 * 1024, remaining));
            const { bytesRead } = await handle.read(block, 0, block.length, null);
            if (!bytesRead)
                break;
            coverage.bytes_read += bytesRead;
            pending = Buffer.concat([pending, block.subarray(0, bytesRead)]);
            let newline;
            while ((newline = pending.indexOf(10)) >= 0) {
                if (newline > 1024 * 1024)
                    throw new Error('Log line exceeds 1 MiB');
                yield pending.subarray(0, newline).toString('utf-8');
                pending = pending.subarray(newline + 1);
            }
            if (pending.length > 1024 * 1024)
                throw new Error('Log line exceeds 1 MiB');
        }
        if (pending.length)
            yield pending.toString('utf-8');
    }
    finally {
        await handle.close();
    }
}
/**
 * Parse JSONL log lines (structured format).
 */
function parseJsonlLines(lines, sourceFile) {
    const entries = [];
    for (const line of lines) {
        try {
            const obj = JSON.parse(line);
            // Support common structured log formats
            const timestamp = obj.ts
                ? new Date(typeof obj.ts === 'number' ? obj.ts : obj.ts).toISOString()
                : obj.timestamp
                    ? new Date(obj.timestamp).toISOString()
                    : obj.time
                        ? new Date(obj.time).toISOString()
                        : new Date().toISOString();
            const level = normalizeLevel(obj.level || obj.severity || obj.lvl || 'info');
            const message = obj.msg || obj.message || obj.op
                ? `${obj.op || ''}${obj.msg || obj.message || ''}${obj.error?.message ? ` — ${obj.error.message}` : ''}`.trim()
                : JSON.stringify(obj).slice(0, 200);
            entries.push({
                timestamp,
                level,
                message,
                source_file: sourceFile,
            });
        }
        catch {
            // Skip malformed lines
        }
    }
    return entries;
}
/**
 * Parse plain text log lines using regex patterns.
 */
function parseTextLines(lines, sourceFile) {
    const entries = [];
    // Common patterns:
    // [2024-01-15T10:30:00.000Z] ERROR something happened
    // 2024-01-15 10:30:00 [ERROR] something happened
    // ERROR: something happened
    const timestampPattern = /(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)/;
    const levelPattern = /\b(debug|info|warn(?:ing)?|error|fatal|DEBU[G]?|INFO|WARN(?:ING)?|ERROR|FATAL)\b/i;
    for (const line of lines) {
        const tsMatch = line.match(timestampPattern);
        const lvlMatch = line.match(levelPattern);
        const timestamp = tsMatch
            ? new Date(tsMatch[1]).toISOString()
            : new Date().toISOString();
        const level = lvlMatch ? normalizeLevel(lvlMatch[1]) : 'info';
        // Remove timestamp and level from message
        let message = line;
        if (tsMatch)
            message = message.replace(tsMatch[0], '');
        if (lvlMatch)
            message = message.replace(lvlMatch[0], '');
        message = message.replace(/^\s*[\[\]():|-]+\s*/, '').trim();
        if (message) {
            entries.push({
                timestamp,
                level,
                message,
                source_file: sourceFile,
                raw: line,
            });
        }
    }
    return entries;
}
// ============================================================================
// HELPERS
// ============================================================================
/**
 * Normalize log level strings to standard severity.
 */
function normalizeLevel(level) {
    const l = level.toLowerCase();
    if (l === 'fatal')
        return 'fatal';
    if (l === 'error' || l === 'err')
        return 'error';
    if (l === 'warn' || l === 'warning')
        return 'warn';
    if (l === 'info')
        return 'info';
    if (l === 'debug' || l === 'trace' || l === 'verbose')
        return 'debug';
    return 'info';
}
/**
 * Parse relative time strings ('1h', '30m', '7d') or ISO 8601 to timestamp.
 */
function parseRelativeTime(input) {
    // Try ISO 8601 first
    const date = new Date(input);
    if (!isNaN(date.getTime()))
        return date.getTime();
    // Parse relative
    const match = input.match(/^(\d+)\s*(s|m|h|d|w)$/);
    if (!match)
        throw new Error(`Invalid time format: ${input}. Use ISO 8601 or relative (1h, 30m, 7d)`);
    const value = parseInt(match[1], 10);
    const unit = match[2];
    const multipliers = {
        s: 1000,
        m: 60 * 1000,
        h: 60 * 60 * 1000,
        d: 24 * 60 * 60 * 1000,
        w: 7 * 24 * 60 * 60 * 1000,
    };
    return Date.now() - value * multipliers[unit];
}
//# sourceMappingURL=log-reader.js.map