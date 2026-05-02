"use strict";
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
exports.generateTraceId = generateTraceId;
exports.generateCollectionId = generateCollectionId;
exports.storeTrace = storeTrace;
exports.loadTraceSummary = loadTraceSummary;
exports.loadFullTrace = loadFullTrace;
exports.loadAllTraces = loadAllTraces;
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
const path_1 = __importDefault(require("path"));
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
    const random = Math.random().toString(36).substring(2, 6);
    return `TRC_${dateStr}_${timeStr}_${random}`;
}
/**
 * Generate a collection ID
 */
function generateCollectionId() {
    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
    const timeStr = now.toISOString().slice(11, 19).replace(/:/g, '');
    const random = Math.random().toString(36).substring(2, 4);
    return `COL_${dateStr}_${timeStr}_${random}`;
}
/**
 * Store a trace with indexing
 */
async function storeTrace(trace, rawData, config) {
    const paths = (0, config_1.getTracePaths)(config);
    // Ensure directories exist
    await promises_1.default.mkdir(paths.traces, { recursive: true });
    await promises_1.default.mkdir(path_1.default.join(paths.raw, trace.source), { recursive: true });
    // Store raw data if provided
    if (rawData) {
        const rawPath = path_1.default.join(paths.raw, trace.source, `${trace.trace_id}.json`);
        await promises_1.default.writeFile(rawPath, JSON.stringify(rawData, null, 2), 'utf-8');
        trace.full_data_path = rawPath;
        trace.has_full_data = true;
    }
    // Update index
    await updateTraceIndex(trace, config);
    return { trace_id: trace.trace_id, file_path: trace.full_data_path || '' };
}
/**
 * Load trace summary by ID
 */
async function loadTraceSummary(trace_id, config) {
    const index = await loadTraceIndex(config);
    const entry = index.traces[trace_id];
    if (!entry)
        return null;
    return reconstructTraceFromIndex(entry);
}
/**
 * Load full trace data (lazy loading)
 */
async function loadFullTrace(trace_id, config) {
    const trace = await loadTraceSummary(trace_id, config);
    if (!trace?.full_data_path)
        return null;
    try {
        const content = await promises_1.default.readFile(trace.full_data_path, 'utf-8');
        return JSON.parse(content);
    }
    catch (error) {
        if (error.code === 'ENOENT') {
            return null;
        }
        throw error;
    }
}
/**
 * Load all traces (summaries only)
 */
async function loadAllTraces(config) {
    const index = await loadTraceIndex(config);
    return Object.values(index.traces).map(reconstructTraceFromIndex);
}
/**
 * Load traces by source
 */
async function loadTracesBySource(source, config) {
    const index = await loadTraceIndex(config);
    const traceIds = index.by_source[source] || [];
    return traceIds
        .map((id) => index.traces[id])
        .filter(Boolean)
        .map(reconstructTraceFromIndex);
}
/**
 * Load traces by category
 */
async function loadTracesByCategory(category, config) {
    const index = await loadTraceIndex(config);
    const traceIds = index.by_category[category] || [];
    return traceIds
        .map((id) => index.traces[id])
        .filter(Boolean)
        .map(reconstructTraceFromIndex);
}
/**
 * Load traces by severity
 */
async function loadTracesBySeverity(severity, config) {
    const index = await loadTraceIndex(config);
    const traceIds = index.by_severity[severity] || [];
    return traceIds
        .map((id) => index.traces[id])
        .filter(Boolean)
        .map(reconstructTraceFromIndex);
}
/**
 * Load traces correlated to an incident
 */
async function loadTracesForIncident(incident_id, config) {
    const index = await loadTraceIndex(config);
    const traceIds = index.by_incident[incident_id] || [];
    return traceIds
        .map((id) => index.traces[id])
        .filter(Boolean)
        .map(reconstructTraceFromIndex);
}
// ============================================================================
// INDEX MANAGEMENT
// ============================================================================
/**
 * Load or initialize trace index
 */
async function loadTraceIndex(config) {
    const paths = (0, config_1.getTracePaths)(config);
    try {
        const content = await promises_1.default.readFile(paths.index, 'utf-8');
        return JSON.parse(content);
    }
    catch (error) {
        if (error.code === 'ENOENT') {
            return createEmptyIndex();
        }
        throw error;
    }
}
/**
 * Create empty index
 */
function createEmptyIndex() {
    return {
        version: '1.0.0',
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
/**
 * Update trace index with new trace
 */
async function updateTraceIndex(trace, config) {
    const paths = (0, config_1.getTracePaths)(config);
    const index = await loadTraceIndex(config);
    // Create index entry
    const entry = {
        trace_id: trace.trace_id,
        source: trace.source,
        timestamp: trace.timestamp,
        severity: trace.severity,
        category: trace.category,
        operation: trace.operation,
        summary_preview: trace.summary.description.substring(0, 100),
        has_error: trace.severity === 'error' || trace.severity === 'fatal',
        incident_ids: trace.incident_ids || [],
    };
    // Add to main index
    index.traces[trace.trace_id] = entry;
    index.total_traces++;
    index.last_updated = Date.now();
    // Update by_source index
    if (!index.by_source[trace.source]) {
        index.by_source[trace.source] = [];
    }
    index.by_source[trace.source].push(trace.trace_id);
    // Update by_category index
    if (!index.by_category[trace.category]) {
        index.by_category[trace.category] = [];
    }
    index.by_category[trace.category].push(trace.trace_id);
    // Update by_severity index
    if (!index.by_severity[trace.severity]) {
        index.by_severity[trace.severity] = [];
    }
    index.by_severity[trace.severity].push(trace.trace_id);
    // Update by_date index
    const dateKey = new Date(trace.timestamp).toISOString().slice(0, 10);
    if (!index.by_date[dateKey]) {
        index.by_date[dateKey] = [];
    }
    index.by_date[dateKey].push(trace.trace_id);
    // Update by_incident index
    for (const incidentId of trace.incident_ids || []) {
        if (!index.by_incident[incidentId]) {
            index.by_incident[incidentId] = [];
        }
        index.by_incident[incidentId].push(trace.trace_id);
    }
    // Save index
    await promises_1.default.mkdir(path_1.default.dirname(paths.index), { recursive: true });
    await promises_1.default.writeFile(paths.index, JSON.stringify(index, null, 2), 'utf-8');
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
        has_full_data: true,
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
    const traces = await Promise.all(traceIds.map((id) => loadTraceSummary(id, config)));
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
    await promises_1.default.mkdir(paths.collections, { recursive: true });
    const filepath = path_1.default.join(paths.collections, `${collection.collection_id}.json`);
    await promises_1.default.writeFile(filepath, JSON.stringify(collection, null, 2), 'utf-8');
    return collection;
}
/**
 * Load a collection by ID
 */
async function loadCollection(collection_id, config) {
    const paths = (0, config_1.getTracePaths)(config);
    const filepath = path_1.default.join(paths.collections, `${collection_id}.json`);
    try {
        const content = await promises_1.default.readFile(filepath, 'utf-8');
        return JSON.parse(content);
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
        start: Math.min(...timestamps),
        end: Math.max(...timestamps),
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
    const index = await loadTraceIndex(config);
    // Update trace entry
    const entry = index.traces[trace_id];
    if (entry) {
        if (!entry.incident_ids.includes(incident_id)) {
            entry.incident_ids.push(incident_id);
        }
    }
    // Update by_incident index
    if (!index.by_incident[incident_id]) {
        index.by_incident[incident_id] = [];
    }
    if (!index.by_incident[incident_id].includes(trace_id)) {
        index.by_incident[incident_id].push(trace_id);
    }
    // Save index
    const paths = (0, config_1.getTracePaths)(config);
    await promises_1.default.writeFile(paths.index, JSON.stringify(index, null, 2), 'utf-8');
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
        by_source: bySource,
        by_severity: bySeverity,
        oldest_trace: timestamps.length > 0 ? Math.min(...timestamps) : 0,
        newest_trace: timestamps.length > 0 ? Math.max(...timestamps) : 0,
    };
}
//# sourceMappingURL=storage.js.map