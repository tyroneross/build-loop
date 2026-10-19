"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Trace Storage Module
 *
 * Handles reading/writing traces to filesystem with indexing.
 * Supports lazy loading of full trace data.
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.PartialTraceError = void 0;
exports.generateTraceId = generateTraceId;
exports.generateCollectionId = generateCollectionId;
exports.storeTrace = storeTrace;
exports.loadTraceSummary = loadTraceSummary;
exports.loadFullTrace = loadFullTrace;
exports.loadAllTraces = loadAllTraces;
exports.loadTraceResults = loadTraceResults;
exports.loadTracesBySource = loadTracesBySource;
exports.loadTracesByCategory = loadTracesByCategory;
exports.loadTracesBySeverity = loadTracesBySeverity;
exports.loadTracesForIncident = loadTracesForIncident;
exports.loadTraceIndex = loadTraceIndex;
exports.createCollection = createCollection;
exports.loadCollection = loadCollection;
exports.correlateTraceToIncident = correlateTraceToIncident;
exports.getTraceStats = getTraceStats;
const promises_1 = __importDefault(require("fs/promises"));
const fs_1 = require("fs");
const path_1 = __importDefault(require("path"));
const crypto_1 = require("crypto");
const config_1 = require("../config");
// ============================================================================
// TRACE STORAGE
// ============================================================================
/**
 * Generate a unique trace ID
 */
function generateTraceId() {
    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
    const timeStr = now.toISOString().slice(11, 19).replace(/:/g, '');
    const random = (0, crypto_1.randomUUID)();
    return `TRC_${dateStr}_${timeStr}_${random}`;
}
/**
 * Generate a collection ID
 */
function generateCollectionId() {
    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
    const timeStr = now.toISOString().slice(11, 19).replace(/:/g, '');
    const random = (0, crypto_1.randomUUID)();
    return `COL_${dateStr}_${timeStr}_${random}`;
}
/**
 * Store a trace with indexing
 */
async function storeTrace(trace, rawData, config) {
    validateTrace(trace);
    const paths = (0, config_1.getTracePaths)(config);
    const metadata = path_1.default.join(paths.traces, 'metadata');
    await containedDirectory(paths.traces, metadata);
    const stored = { ...trace, has_full_data: false };
    delete stored.storage_coverage;
    delete stored.full_data_path;
    if (rawData !== undefined) {
        const content = JSON.stringify(rawData);
        if (content === undefined)
            throw new Error('Raw trace is not JSON serializable');
        const rawDir = path_1.default.join(paths.raw, trace.source);
        await containedDirectory(paths.traces, rawDir);
        // Immutable revision avoids concurrent same-ID writes pairing different raw data.
        const digest = (0, crypto_1.createHash)('sha256').update(content).digest('hex');
        stored.full_data_path = path_1.default.join(rawDir, `${trace.trace_id}.${digest}.json`);
        if (Buffer.byteLength(content) > 64 * 1024 * 1024)
            throw new Error('Raw trace exceeds 64 MiB');
        await atomicJson(stored.full_data_path, content);
        stored.has_full_data = true;
    }
    // Publication point: incomplete writes leave only unreferenced raw revisions.
    const serialized = JSON.stringify(stored);
    if (Buffer.byteLength(serialized) > 1024 * 1024)
        throw new Error('Trace metadata exceeds 1 MiB');
    await atomicJson(path_1.default.join(metadata, `${trace.trace_id}.json`), serialized);
    trace.full_data_path = stored.full_data_path;
    trace.has_full_data = stored.has_full_data;
    return { trace_id: trace.trace_id, file_path: stored.full_data_path || '' };
}
/**
 * Load trace summary by ID
 */
async function loadTraceSummary(trace_id, config) {
    validateId(trace_id);
    const snapshot = await loadTraceSnapshot(config, { traceId: trace_id });
    // A valid canonical record remains usable when optional legacy correlation
    // input is unavailable. Its returned object explicitly carries that omission.
    if (snapshot.index.coverage.invalid.length)
        requireComplete(snapshot);
    return snapshot.traces[trace_id] || null;
}
/**
 * Load full trace data (lazy loading)
 */
async function loadFullTrace(trace_id, config) {
    const trace = await loadTraceSummary(trace_id, config);
    if (!trace?.full_data_path)
        return null;
    const paths = (0, config_1.getTracePaths)(config);
    const locator = safeRawPath(trace, paths.raw);
    await assertContained(paths.traces, locator);
    return await readJson(locator, 64 * 1024 * 1024);
}
/**
 * Load all traces (summaries only)
 */
async function loadAllTraces(config) {
    const snapshot = await loadTraceSnapshot(config);
    requireComplete(snapshot);
    return Object.values(snapshot.traces);
}
/** Explicit partial-result API. Legacy array APIs remain strict so JSON cannot
 * silently discard coverage attached to an array. Callers must adopt this API
 * to display healthy rows alongside invalid-record coverage. */
async function loadTraceResults(config) {
    const snapshot = await loadTraceSnapshot(config);
    return { traces: Object.values(snapshot.traces), coverage: snapshot.index.coverage };
}
class PartialTraceError extends Error {
    constructor(coverage, traces) {
        super(`Incomplete trace coverage: ${coverage.invalid.map(item => item.reason).join('; ')}`);
        this.coverage = coverage;
        this.traces = traces;
        this.name = 'PartialTraceError';
    }
}
exports.PartialTraceError = PartialTraceError;
function requireComplete(snapshot) {
    if (!snapshot.index.coverage.complete) {
        throw new PartialTraceError(snapshot.index.coverage, Object.values(snapshot.traces));
    }
}
/**
 * Load traces by source
 */
async function loadTracesBySource(source, config) {
    const snapshot = await loadTraceSnapshot(config);
    requireComplete(snapshot);
    const { index, traces } = snapshot;
    const traceIds = index.by_source[source] || [];
    return traceIds
        .map((id) => traces[id]).filter(Boolean);
}
/**
 * Load traces by category
 */
async function loadTracesByCategory(category, config) {
    const snapshot = await loadTraceSnapshot(config);
    requireComplete(snapshot);
    const { index, traces } = snapshot;
    const traceIds = index.by_category[category] || [];
    return traceIds
        .map((id) => traces[id]).filter(Boolean);
}
/**
 * Load traces by severity
 */
async function loadTracesBySeverity(severity, config) {
    const snapshot = await loadTraceSnapshot(config);
    requireComplete(snapshot);
    const { index, traces } = snapshot;
    const traceIds = index.by_severity[severity] || [];
    return traceIds
        .map((id) => traces[id]).filter(Boolean);
}
/**
 * Load traces correlated to an incident
 */
async function loadTracesForIncident(incident_id, config) {
    validateId(incident_id);
    const snapshot = await loadTraceSnapshot(config);
    requireComplete(snapshot);
    const { index, traces } = snapshot;
    const traceIds = index.by_incident[incident_id] || [];
    return traceIds
        .map((id) => traces[id]).filter(Boolean);
}
// ============================================================================
// INDEX MANAGEMENT
// ============================================================================
/**
 * Load or initialize trace index
 */
async function loadTraceIndex(config) {
    return (await loadTraceSnapshot(config, { indexOnly: true })).index;
}
/** Read each relevant metadata record once. Failures belong to a record, not
 * its healthy neighbors. Index-only callers discard full objects immediately. */
async function loadTraceSnapshot(config, options = {}) {
    const paths = (0, config_1.getTracePaths)(config);
    const index = createEmptyIndex();
    const canonicalDirect = options.traceId && await pathPresent(path_1.default.join(paths.traces, 'metadata', `${options.traceId}.json`));
    if (canonicalDirect)
        index.coverage.scope = 'canonical_trace_and_raw_availability';
    const entries = Object.create(null);
    const traces = Object.create(null);
    const invalid = (file, error, id) => {
        index.coverage.complete = false;
        index.coverage.invalid.push({ path: file, trace_id: id,
            reason: error instanceof Error ? error.message : String(error) });
    };
    const legacyFailure = (error, id) => {
        var _a;
        if (!canonicalDirect) {
            invalid(paths.index, error, id);
            return;
        }
        index.coverage.complete = false;
        ((_a = index.coverage).omitted || (_a.omitted = [])).push({ path: paths.index,
            reason: error instanceof Error ? error.message : String(error) });
    };
    // The legacy aggregate is compatibility input, never a publication owner.
    if (await pathPresent(paths.index)) {
        try {
            await assertContained(paths.traces, paths.index);
            const legacy = await readJson(paths.index, 64 * 1024 * 1024);
            if (!legacy || !legacy.traces || typeof legacy.traces !== 'object' || Array.isArray(legacy.traces)) {
                throw new Error('Invalid legacy trace index');
            }
            for (const [id, entry] of Object.entries(legacy.traces)) {
                if (options.traceId && id !== options.traceId)
                    continue;
                try {
                    validateId(id);
                    if (!entry || entry.trace_id !== id)
                        throw new Error('Legacy identity mismatch');
                    validateTrace(reconstructTraceFromIndex(entry));
                    if (!Array.isArray(entry.incident_ids))
                        throw new Error('Invalid legacy incident IDs');
                    entry.incident_ids.forEach(validateId);
                    const raw = path_1.default.join(paths.raw, entry.source, `${id}.json`);
                    const available = await pathPresent(raw);
                    if (available)
                        await validateRawFile(paths.traces, raw);
                    entries[id] = { ...entry, has_full_data: available, full_data_path: available ? raw : undefined };
                }
                catch (error) {
                    legacyFailure(error, id);
                }
            }
        }
        catch (error) {
            legacyFailure(error);
        }
    }
    const metadata = path_1.default.join(paths.traces, 'metadata');
    let filenames = [];
    if (options.traceId)
        filenames = [`${options.traceId}.json`];
    else if (await pathPresent(metadata)) {
        try {
            await assertContained(paths.traces, metadata);
            filenames = await promises_1.default.readdir(metadata);
        }
        catch (error) {
            invalid(metadata, error);
        }
    }
    for (const filename of filenames) {
        if (!filename.endsWith('.json'))
            continue;
        const id = filename.slice(0, -5), file = path_1.default.join(metadata, filename);
        try {
            validateId(id);
            if (!await pathPresent(file))
                continue;
            await assertContained(paths.traces, file);
            const trace = await readJson(file, 1024 * 1024);
            validateTrace(trace);
            delete trace.storage_coverage;
            if (trace.trace_id !== id)
                throw new Error('Trace metadata identity mismatch');
            if (trace.has_full_data) {
                const raw = safeRawPath(trace, paths.raw);
                await validateRawFile(paths.traces, raw);
                trace.full_data_path = raw;
            }
            else
                delete trace.full_data_path;
            const entry = indexEntry(trace);
            entry.incident_ids = [...new Set([...(entries[id]?.incident_ids || []), ...entry.incident_ids])];
            entries[id] = entry;
            if (!options.indexOnly)
                traces[id] = trace;
        }
        catch (error) {
            // A corrupt canonical record must never resurrect an obsolete legacy row.
            delete entries[id];
            delete traces[id];
            invalid(file, error, id);
        }
    }
    const correlations = path_1.default.join(paths.traces, 'correlations');
    if (await pathPresent(correlations)) {
        try {
            await assertContained(paths.traces, correlations);
            const ids = options.traceId ? [options.traceId] : await promises_1.default.readdir(correlations);
            for (const traceId of ids) {
                const directory = path_1.default.join(correlations, traceId);
                try {
                    validateId(traceId);
                    if (!await pathPresent(directory))
                        continue;
                    await assertContained(paths.traces, directory);
                    for (const filename of await promises_1.default.readdir(directory)) {
                        if (!filename.endsWith('.json'))
                            continue;
                        const file = path_1.default.join(directory, filename), incidentId = filename.slice(0, -5);
                        try {
                            validateId(incidentId);
                            await assertContained(paths.traces, file);
                            const edge = await readJson(file, 1024);
                            if (!edge || edge.trace_id !== traceId || edge.incident_id !== incidentId)
                                throw new Error('Correlation identity mismatch');
                            const entry = entries[traceId];
                            if (!entry)
                                throw new Error('Correlation references absent or invalid trace metadata');
                            if (!entry.incident_ids.includes(incidentId))
                                entry.incident_ids.push(incidentId);
                        }
                        catch (error) {
                            invalid(file, error, traceId);
                        }
                    }
                }
                catch (error) {
                    invalid(directory, error, traceId);
                }
            }
        }
        catch (error) {
            invalid(correlations, error);
        }
    }
    for (const entry of Object.values(entries)) {
        addEntry(index, entry);
        if (!options.indexOnly) {
            const trace = traces[entry.trace_id] || reconstructTraceFromIndex(entry);
            trace.incident_ids = [...entry.incident_ids];
            trace.has_full_data = entry.has_full_data === true;
            if (entry.full_data_path)
                trace.full_data_path = entry.full_data_path;
            else
                delete trace.full_data_path;
            if (canonicalDirect && index.coverage.omitted?.length)
                trace.storage_coverage = index.coverage;
            traces[entry.trace_id] = trace;
        }
    }
    return { index, traces };
}
/** lstat distinguishes an absent path from a dangling symlink (corruption). */
async function pathPresent(file) {
    try {
        await promises_1.default.lstat(file);
        return true;
    }
    catch (error) {
        if (error.code === 'ENOENT')
            return false;
        throw error;
    }
}
async function validateRawFile(root, file) {
    await assertContained(root, file);
    const handle = await promises_1.default.open(file, fs_1.constants.O_RDONLY | fs_1.constants.O_NONBLOCK);
    try {
        const stat = await handle.stat();
        if (!stat.isFile() || stat.size > 64 * 1024 * 1024)
            throw new Error('Invalid or oversized raw trace file');
    }
    finally {
        await handle.close();
    }
}
/**
 * Create empty index
 */
function createEmptyIndex() {
    return {
        version: '1.0.0',
        coverage: { complete: true, scope: 'metadata_and_raw_availability', invalid: [] },
        last_updated: Date.now(),
        total_traces: 0,
        traces: {},
        by_source: {},
        by_category: {},
        by_severity: {},
        by_date: {},
        by_incident: {},
    };
}
const TRACE_SOURCES = new Set(['opentelemetry', 'sentry', 'langchain',
    'chrome-devtools', 'playwright', 'browser-console', 'internal']);
const TRACE_SEVERITIES = new Set(['debug', 'info', 'warning', 'error', 'fatal']);
const TRACE_CATEGORIES = new Set(['http-request', 'http-response', 'database-query', 'cache-operation',
    'llm-call', 'llm-chain', 'ui-interaction', 'ui-render', 'console-log', 'console-error',
    'network-request', 'performance-metric', 'custom']);
function validateTrace(trace) {
    if (!trace || typeof trace !== 'object')
        throw new Error('Invalid trace object');
    validateId(trace.trace_id);
    validateSource(trace.source);
    if (!Number.isFinite(trace.timestamp) || !Number.isFinite(new Date(trace.timestamp).getTime()) ||
        !TRACE_SEVERITIES.has(trace.severity) || !TRACE_CATEGORIES.has(trace.category) ||
        typeof trace.operation !== 'string' || !trace.operation.trim() ||
        typeof trace.has_full_data !== 'boolean' ||
        !trace.summary || typeof trace.summary.description !== 'string' ||
        !Array.isArray(trace.summary.highlights) || !trace.summary.highlights.every(v => v && typeof v === 'object' && typeof v.label === 'string' && typeof v.value === 'string') ||
        (trace.incident_ids !== undefined && (!Array.isArray(trace.incident_ids) ||
            !trace.incident_ids.every(v => typeof v === 'string' && v.length > 0))) ||
        !Number.isFinite(trace.tokens_estimated) || trace.tokens_estimated < 0 ||
        (trace.duration_ms !== undefined && (!Number.isFinite(trace.duration_ms) || trace.duration_ms < 0))) {
        throw new Error('Invalid persisted trace shape');
    }
    trace.incident_ids?.forEach(validateId);
}
function validateId(id) {
    if (typeof id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(id) ||
        ['__proto__', 'constructor', 'prototype'].includes(id))
        throw new Error('Invalid storage ID');
}
function validateSource(source) {
    if (!TRACE_SOURCES.has(source))
        throw new Error('Invalid trace source');
}
async function exists(file) {
    try {
        await promises_1.default.access(file);
        return true;
    }
    catch (error) {
        if (error.code === 'ENOENT')
            return false;
        throw error;
    }
}
async function assertContained(root, file) {
    const [realRoot, realFile] = await Promise.all([promises_1.default.realpath(root), promises_1.default.realpath(file)]);
    const relative = path_1.default.relative(realRoot, realFile);
    if (relative === '..' || relative.startsWith(`..${path_1.default.sep}`) || path_1.default.isAbsolute(relative)) {
        throw new Error('Storage path escapes trace root');
    }
}
async function containedDirectory(root, dir) {
    await promises_1.default.mkdir(root, { recursive: true });
    // Check each existing ancestor before mkdir can follow an escaping symlink.
    let current = path_1.default.resolve(root);
    for (const part of path_1.default.relative(current, dir).split(path_1.default.sep).filter(Boolean)) {
        current = path_1.default.join(current, part);
        if (await exists(current))
            await assertContained(root, current);
        else {
            try {
                await promises_1.default.mkdir(current);
            }
            catch (error) {
                if (error.code !== 'EEXIST')
                    throw error;
            }
            await assertContained(root, current);
        }
    }
    await assertContained(root, dir);
}
async function readJson(file, maxBytes) {
    const handle = await promises_1.default.open(file, fs_1.constants.O_RDONLY | fs_1.constants.O_NONBLOCK);
    try {
        const metadata = await handle.stat();
        if (!metadata.isFile() || metadata.size > maxBytes)
            throw new Error('Invalid or oversized trace file');
        const chunks = [];
        let total = 0;
        while (true) {
            const block = Buffer.allocUnsafe(Math.min(64 * 1024, maxBytes + 1 - total));
            const { bytesRead } = await handle.read(block, 0, block.length, null);
            if (!bytesRead)
                break;
            total += bytesRead;
            if (total > maxBytes)
                throw new Error('Trace file exceeds read budget');
            chunks.push(block.subarray(0, bytesRead));
        }
        return JSON.parse(Buffer.concat(chunks, total).toString('utf-8'));
    }
    finally {
        await handle.close();
    }
}
async function atomicJson(file, content) {
    const temp = `${file}.${(0, crypto_1.randomUUID)()}.tmp`;
    let handle;
    try {
        handle = await promises_1.default.open(temp, 'wx', 0o600);
        await handle.writeFile(content, 'utf-8');
        await handle.sync();
        await handle.close();
        handle = undefined;
        await promises_1.default.rename(temp, file);
    }
    finally {
        await handle?.close();
        await promises_1.default.rm(temp, { force: true });
    }
}
function safeRawPath(trace, rawRoot) {
    validateId(trace.trace_id);
    validateSource(trace.source);
    const basename = path_1.default.basename(trace.full_data_path || '');
    const escapedId = trace.trace_id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (!new RegExp(`^${escapedId}(?:\\.[a-f0-9]{64})?\\.json$`).test(basename)) {
        throw new Error('Invalid raw revision locator');
    }
    return path_1.default.join(rawRoot, trace.source, basename);
}
function indexEntry(trace) {
    return { trace_id: trace.trace_id, source: trace.source, timestamp: trace.timestamp,
        severity: trace.severity, category: trace.category, operation: trace.operation,
        summary_preview: trace.summary.description.substring(0, 100),
        has_error: trace.severity === 'error' || trace.severity === 'fatal',
        incident_ids: trace.incident_ids || [], has_full_data: trace.has_full_data,
        full_data_path: trace.full_data_path };
}
function addEntry(index, entry) {
    index.traces[entry.trace_id] = entry;
    index.total_traces++;
    for (const [map, key] of [[index.by_source, entry.source], [index.by_category, entry.category],
        [index.by_severity, entry.severity], [index.by_date, new Date(entry.timestamp).toISOString().slice(0, 10)],
        ...entry.incident_ids.map(id => [index.by_incident, id])]) {
        if (!Object.prototype.hasOwnProperty.call(map, key))
            Object.defineProperty(map, key, { value: [], enumerable: true });
        map[key].push(entry.trace_id);
    }
}
/**
 * Reconstruct trace from index entry
 */
function reconstructTraceFromIndex(entry) {
    return {
        trace_id: entry.trace_id,
        source: entry.source,
        timestamp: entry.timestamp,
        severity: entry.severity,
        category: entry.category,
        operation: entry.operation,
        summary: {
            description: entry.summary_preview,
            highlights: [],
        },
        incident_ids: entry.incident_ids,
        has_full_data: entry.has_full_data === true,
        full_data_path: entry.full_data_path,
        tokens_estimated: 50,
    };
}
// ============================================================================
// COLLECTION MANAGEMENT
// ============================================================================
/**
 * Create a trace collection
 */
async function createCollection(name, traceIds, incident_id, config) {
    const paths = (0, config_1.getTracePaths)(config);
    // Load traces to generate summary
    const snapshot = await loadTraceSnapshot(config);
    requireComplete(snapshot);
    const traces = traceIds.map(id => {
        validateId(id);
        return snapshot.traces[id] || null;
    });
    const validTraces = traces.filter(Boolean);
    // Generate summary
    const summary = generateCollectionSummary(validTraces);
    const collection = {
        collection_id: generateCollectionId(),
        name,
        traces: traceIds,
        created_at: Date.now(),
        incident_id,
        summary,
    };
    // Save collection
    await containedDirectory(paths.traces, paths.collections);
    const filepath = path_1.default.join(paths.collections, `${collection.collection_id}.json`);
    const serialized = JSON.stringify(collection, null, 2);
    if (Buffer.byteLength(serialized) > 1024 * 1024)
        throw new Error('Collection exceeds 1 MiB');
    await atomicJson(filepath, serialized);
    return collection;
}
/**
 * Load a collection by ID
 */
async function loadCollection(collection_id, config) {
    validateId(collection_id);
    const paths = (0, config_1.getTracePaths)(config);
    const filepath = path_1.default.join(paths.collections, `${collection_id}.json`);
    try {
        await assertContained(paths.traces, filepath);
        return await readJson(filepath, 1024 * 1024);
    }
    catch (error) {
        if (error.code === 'ENOENT') {
            return null;
        }
        throw error;
    }
}
/**
 * Generate collection summary from traces
 */
function generateCollectionSummary(traces) {
    const bySeverity = {
        debug: 0,
        info: 0,
        warning: 0,
        error: 0,
        fatal: 0,
    };
    const byCategory = {};
    let totalErrors = 0;
    let slowestOperation;
    let slowestDuration = 0;
    const timestamps = traces.map((t) => t.timestamp);
    const timeRange = {
        start: timestamps.length ? Math.min(...timestamps) : 0,
        end: timestamps.length ? Math.max(...timestamps) : 0,
    };
    for (const trace of traces) {
        // Count by severity
        bySeverity[trace.severity]++;
        // Count by category
        byCategory[trace.category] = (byCategory[trace.category] || 0) + 1;
        // Count errors
        if (trace.severity === 'error' || trace.severity === 'fatal') {
            totalErrors++;
        }
        // Track slowest operation
        const duration = trace.duration_ms || 0;
        if (duration > slowestDuration) {
            slowestDuration = duration;
            slowestOperation = trace.operation;
        }
    }
    return {
        total_traces: traces.length,
        by_severity: bySeverity,
        by_category: byCategory,
        time_range: timeRange,
        total_errors: totalErrors,
        slowest_operation: slowestOperation,
    };
}
// ============================================================================
// CORRELATION
// ============================================================================
/**
 * Correlate a trace to an incident
 */
async function correlateTraceToIncident(trace_id, incident_id, config) {
    validateId(trace_id);
    validateId(incident_id);
    if (!await loadTraceSummary(trace_id, config))
        return;
    const paths = (0, config_1.getTracePaths)(config);
    const directory = path_1.default.join(paths.traces, 'correlations', trace_id);
    await containedDirectory(paths.traces, directory);
    await atomicJson(path_1.default.join(directory, `${incident_id}.json`), JSON.stringify({ trace_id, incident_id }));
}
/**
 * Get trace statistics
 */
async function getTraceStats(config) {
    const index = await loadTraceIndex(config);
    const bySeverity = {};
    const bySource = {};
    for (const [source, ids] of Object.entries(index.by_source)) {
        bySource[source] = ids.length;
    }
    for (const [severity, ids] of Object.entries(index.by_severity)) {
        bySeverity[severity] = ids.length;
    }
    const timestamps = Object.values(index.traces).map((t) => t.timestamp);
    return {
        total_traces: index.total_traces,
        coverage: index.coverage,
        by_source: bySource,
        by_severity: bySeverity,
        oldest_trace: timestamps.length > 0 ? Math.min(...timestamps) : 0,
        newest_trace: timestamps.length > 0 ? Math.max(...timestamps) : 0,
    };
}
//# sourceMappingURL=storage.js.map