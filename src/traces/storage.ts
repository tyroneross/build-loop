/**
 * Trace Storage Module
 *
 * Handles reading/writing traces to filesystem with indexing.
 * Supports lazy loading of full trace data.
 */

import fs from 'fs/promises';
import path from 'path';
import type {
  UnifiedTrace,
  TraceIndex,
  TraceIndexEntry,
  TraceSource,
  TraceCategory,
  TraceSeverity,
  TraceCollection,
  CollectionSummary,
} from './types';
import type { MemoryConfig } from '../types';
import { getTracePaths } from '../config';

// ============================================================================
// TRACE STORAGE
// ============================================================================

/**
 * Generate a unique trace ID
 */
export function generateTraceId(): string {
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
  const timeStr = now.toISOString().slice(11, 19).replace(/:/g, '');
  const random = Math.random().toString(36).substring(2, 6);

  return `TRC_${dateStr}_${timeStr}_${random}`;
}

/**
 * Generate a collection ID
 */
export function generateCollectionId(): string {
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
  const timeStr = now.toISOString().slice(11, 19).replace(/:/g, '');
  const random = Math.random().toString(36).substring(2, 4);

  return `COL_${dateStr}_${timeStr}_${random}`;
}

/**
 * Store a trace with indexing
 */
export async function storeTrace(
  trace: UnifiedTrace,
  rawData?: unknown,
  config?: MemoryConfig
): Promise<{ trace_id: string; file_path: string }> {
  const paths = getTracePaths(config);

  // Ensure directories exist
  await fs.mkdir(paths.traces, { recursive: true });
  await fs.mkdir(path.join(paths.raw, trace.source), { recursive: true });

  // Store raw data if provided
  if (rawData) {
    const rawPath = path.join(paths.raw, trace.source, `${trace.trace_id}.json`);
    await fs.writeFile(rawPath, JSON.stringify(rawData, null, 2), 'utf-8');
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
export async function loadTraceSummary(
  trace_id: string,
  config?: MemoryConfig
): Promise<UnifiedTrace | null> {
  const index = await loadTraceIndex(config);
  const entry = index.traces[trace_id];

  if (!entry) return null;

  return reconstructTraceFromIndex(entry);
}

/**
 * Load full trace data (lazy loading)
 */
export async function loadFullTrace(
  trace_id: string,
  config?: MemoryConfig
): Promise<unknown | null> {
  const trace = await loadTraceSummary(trace_id, config);

  if (!trace?.full_data_path) return null;

  try {
    const content = await fs.readFile(trace.full_data_path, 'utf-8');
    return JSON.parse(content);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return null;
    }
    throw error;
  }
}

/**
 * Load all traces (summaries only)
 */
export async function loadAllTraces(config?: MemoryConfig): Promise<UnifiedTrace[]> {
  const index = await loadTraceIndex(config);

  return Object.values(index.traces).map(reconstructTraceFromIndex);
}

/**
 * Load traces by source
 */
export async function loadTracesBySource(
  source: TraceSource,
  config?: MemoryConfig
): Promise<UnifiedTrace[]> {
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
export async function loadTracesByCategory(
  category: TraceCategory,
  config?: MemoryConfig
): Promise<UnifiedTrace[]> {
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
export async function loadTracesBySeverity(
  severity: TraceSeverity,
  config?: MemoryConfig
): Promise<UnifiedTrace[]> {
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
export async function loadTracesForIncident(
  incident_id: string,
  config?: MemoryConfig
): Promise<UnifiedTrace[]> {
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
export async function loadTraceIndex(config?: MemoryConfig): Promise<TraceIndex> {
  const paths = getTracePaths(config);

  try {
    const content = await fs.readFile(paths.index, 'utf-8');
    return JSON.parse(content) as TraceIndex;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return createEmptyIndex();
    }
    throw error;
  }
}

/**
 * Create empty index
 */
function createEmptyIndex(): TraceIndex {
  return {
    version: '1.0.0',
    last_updated: Date.now(),
    total_traces: 0,
    traces: {},
    by_source: {} as Record<TraceSource, string[]>,
    by_category: {} as Record<TraceCategory, string[]>,
    by_severity: {} as Record<TraceSeverity, string[]>,
    by_date: {},
    by_incident: {},
  };
}

/**
 * Update trace index with new trace
 */
async function updateTraceIndex(
  trace: UnifiedTrace,
  config?: MemoryConfig
): Promise<void> {
  const paths = getTracePaths(config);
  const index = await loadTraceIndex(config);

  // Create index entry
  const entry: TraceIndexEntry = {
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
  await fs.mkdir(path.dirname(paths.index), { recursive: true });
  await fs.writeFile(paths.index, JSON.stringify(index, null, 2), 'utf-8');
}

/**
 * Reconstruct trace from index entry
 */
function reconstructTraceFromIndex(entry: TraceIndexEntry): UnifiedTrace {
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
export async function createCollection(
  name: string,
  traceIds: string[],
  incident_id?: string,
  config?: MemoryConfig
): Promise<TraceCollection> {
  const paths = getTracePaths(config);

  // Load traces to generate summary
  const traces = await Promise.all(
    traceIds.map((id) => loadTraceSummary(id, config))
  );
  const validTraces = traces.filter(Boolean) as UnifiedTrace[];

  // Generate summary
  const summary = generateCollectionSummary(validTraces);

  const collection: TraceCollection = {
    collection_id: generateCollectionId(),
    name,
    traces: traceIds,
    created_at: Date.now(),
    incident_id,
    summary,
  };

  // Save collection
  await fs.mkdir(paths.collections, { recursive: true });
  const filepath = path.join(paths.collections, `${collection.collection_id}.json`);
  await fs.writeFile(filepath, JSON.stringify(collection, null, 2), 'utf-8');

  return collection;
}

/**
 * Load a collection by ID
 */
export async function loadCollection(
  collection_id: string,
  config?: MemoryConfig
): Promise<TraceCollection | null> {
  const paths = getTracePaths(config);
  const filepath = path.join(paths.collections, `${collection_id}.json`);

  try {
    const content = await fs.readFile(filepath, 'utf-8');
    return JSON.parse(content) as TraceCollection;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return null;
    }
    throw error;
  }
}

/**
 * Generate collection summary from traces
 */
function generateCollectionSummary(traces: UnifiedTrace[]): CollectionSummary {
  const bySeverity: Record<TraceSeverity, number> = {
    debug: 0,
    info: 0,
    warning: 0,
    error: 0,
    fatal: 0,
  };

  const byCategory: Record<string, number> = {};
  let totalErrors = 0;
  let slowestOperation: string | undefined;
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
export async function correlateTraceToIncident(
  trace_id: string,
  incident_id: string,
  config?: MemoryConfig
): Promise<void> {
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
  const paths = getTracePaths(config);
  await fs.writeFile(paths.index, JSON.stringify(index, null, 2), 'utf-8');
}

/**
 * Get trace statistics
 */
export async function getTraceStats(config?: MemoryConfig): Promise<{
  total_traces: number;
  by_source: Record<string, number>;
  by_severity: Record<string, number>;
  oldest_trace: number;
  newest_trace: number;
}> {
  const index = await loadTraceIndex(config);

  const bySeverity: Record<string, number> = {};
  const bySource: Record<string, number> = {};

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
