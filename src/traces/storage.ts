// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Trace Storage Module
 *
 * Handles reading/writing traces to filesystem with indexing.
 * Supports lazy loading of full trace data.
 */

import fs from 'fs/promises';
import { constants } from 'fs';
import path from 'path';
import { createHash, randomUUID } from 'crypto';
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
  validateTrace(trace);
  const paths = getTracePaths(config);
  const metadata = path.join(paths.traces, 'metadata');
  await containedDirectory(paths.traces, metadata);
  const stored: UnifiedTrace = { ...trace, has_full_data: false };
  delete stored.full_data_path;
  if (rawData !== undefined) {
    const content = JSON.stringify(rawData);
    if (content === undefined) throw new Error('Raw trace is not JSON serializable');
    const rawDir = path.join(paths.raw, trace.source);
    await containedDirectory(paths.traces, rawDir);
    // Immutable revision avoids concurrent same-ID writes pairing different raw data.
    const digest = createHash('sha256').update(content).digest('hex');
    stored.full_data_path = path.join(rawDir, `${trace.trace_id}.${digest}.json`);
    if (Buffer.byteLength(content) > 64 * 1024 * 1024) throw new Error('Raw trace exceeds 64 MiB');
    await atomicJson(stored.full_data_path, content);
    stored.has_full_data = true;
  }
  // Publication point: incomplete writes leave only unreferenced raw revisions.
  const serialized = JSON.stringify(stored);
  if (Buffer.byteLength(serialized) > 1024 * 1024) throw new Error('Trace metadata exceeds 1 MiB');
  await atomicJson(path.join(metadata, `${trace.trace_id}.json`), serialized);
  trace.full_data_path = stored.full_data_path;
  trace.has_full_data = stored.has_full_data;
  return { trace_id: trace.trace_id, file_path: stored.full_data_path || '' };
}

/**
 * Load trace summary by ID
 */
export async function loadTraceSummary(
  trace_id: string,
  config?: MemoryConfig
): Promise<UnifiedTrace | null> {
  validateId(trace_id);
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
    const paths = getTracePaths(config);
    const locator = safeRawPath(trace, paths.raw);
    await assertContained(paths.traces, locator);
    return await readJson(locator, 64 * 1024 * 1024);
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
  validateId(incident_id);
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
  let index = createEmptyIndex();
  try {
    await assertContained(paths.traces, paths.index);
    index = await readJson(paths.index, 64 * 1024 * 1024) as TraceIndex;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const entries: Record<string, TraceIndexEntry> = Object.create(null);
  for (const entry of Object.values(index.traces)) {
    validateId(entry.trace_id);
    validateSource(entry.source);
    if (!Array.isArray(entry.incident_ids)) throw new Error('Invalid legacy incident IDs');
    entry.incident_ids.forEach(validateId);
    // Old indexes discarded the locator. Recover only the known contained path.
    const legacyRaw = path.join(paths.raw, entry.source, `${entry.trace_id}.json`);
    let available = false;
    try { await assertContained(paths.traces, legacyRaw); available = (await fs.stat(legacyRaw)).isFile(); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    entries[entry.trace_id] = { ...entry, has_full_data: available,
                              full_data_path: available ? legacyRaw : undefined };
  }
  const metadata = path.join(paths.traces, 'metadata');
  try {
    await assertContained(paths.traces, metadata);
    for (const filename of await fs.readdir(metadata)) {
      if (!filename.endsWith('.json')) continue;
      const id = filename.slice(0, -5);
      validateId(id);
      const file = path.join(metadata, filename);
      await assertContained(paths.traces, file);
      const trace = await readJson(file, 1024 * 1024) as UnifiedTrace;
      validateTrace(trace);
      if (trace.trace_id !== id) throw new Error('Trace metadata identity mismatch');
      if (trace.has_full_data) {
        const raw = safeRawPath(trace, paths.raw);
        await assertContained(paths.traces, raw);
        await fs.access(raw);
        trace.full_data_path = raw;
      } else { delete trace.full_data_path; }
      const entry = indexEntry(trace);
      entry.incident_ids = [...new Set([...(entries[id]?.incident_ids || []), ...entry.incident_ids])];
      entries[id] = entry;
    }
  } catch (error) {
    // Missing metadata directory is the legacy format; missing referenced raw is corruption.
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT' || await exists(metadata)) throw error;
  }
  // Immutable edges have one publication point each; independent writers never
  // overwrite another incident's correlation while rebuilding this projection.
  const correlations = path.join(paths.traces, 'correlations');
  if (await exists(correlations)) {
    await assertContained(paths.traces, correlations);
    for (const traceId of await fs.readdir(correlations)) {
      validateId(traceId);
      const directory = path.join(correlations, traceId);
      await assertContained(paths.traces, directory);
      for (const filename of await fs.readdir(directory)) {
        if (!filename.endsWith('.json')) continue;
        const incidentId = filename.slice(0, -5);
        validateId(incidentId);
        const file = path.join(directory, filename);
        await assertContained(paths.traces, file);
        const edge = await readJson(file, 1024) as {trace_id: string; incident_id: string};
        if (!edge || edge.trace_id !== traceId || edge.incident_id !== incidentId) {
          throw new Error('Correlation identity mismatch');
        }
        const entry = entries[traceId];
        if (entry && !entry.incident_ids.includes(incidentId)) entry.incident_ids.push(incidentId);
      }
    }
  }
  // Derived projection avoids a shared read/modify/write race across processes.
  index = createEmptyIndex();
  for (const entry of Object.values(entries)) addEntry(index, entry);
  return index;
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

const TRACE_SOURCES = new Set<TraceSource>(['opentelemetry', 'sentry', 'langchain',
  'chrome-devtools', 'playwright', 'browser-console', 'internal']);

const TRACE_SEVERITIES = new Set(['debug', 'info', 'warning', 'error', 'fatal']);
const TRACE_CATEGORIES = new Set(['http-request', 'http-response', 'database-query', 'cache-operation',
  'llm-call', 'llm-chain', 'ui-interaction', 'ui-render', 'console-log', 'console-error',
  'network-request', 'performance-metric', 'custom']);
function validateTrace(trace: UnifiedTrace): void {
  if (!trace || typeof trace !== 'object') throw new Error('Invalid trace object');
  validateId(trace.trace_id); validateSource(trace.source);
  if (!Number.isFinite(trace.timestamp) || !Number.isFinite(new Date(trace.timestamp).getTime()) ||
      !TRACE_SEVERITIES.has(trace.severity) || !TRACE_CATEGORIES.has(trace.category) ||
      typeof trace.operation !== 'string' || !trace.operation.trim() ||
      !trace.summary || typeof trace.summary.description !== 'string' ||
      !Array.isArray(trace.summary.highlights) || !trace.summary.highlights.every(v =>
        v && typeof v === 'object' && typeof v.label === 'string' && typeof v.value === 'string') ||
      (trace.incident_ids !== undefined && (!Array.isArray(trace.incident_ids) ||
        !trace.incident_ids.every(v => typeof v === 'string' && v.length > 0))) ||
      !Number.isFinite(trace.tokens_estimated) || trace.tokens_estimated < 0 ||
      (trace.duration_ms !== undefined && (!Number.isFinite(trace.duration_ms) || trace.duration_ms < 0))) {
    throw new Error('Invalid persisted trace shape');
  }
  trace.incident_ids?.forEach(validateId);
}
function validateId(id: string): void {
  if (typeof id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(id) ||
      ['__proto__', 'constructor', 'prototype'].includes(id)) throw new Error('Invalid storage ID');
}
function validateSource(source: TraceSource): void {
  if (!TRACE_SOURCES.has(source)) throw new Error('Invalid trace source');
}
async function exists(file: string): Promise<boolean> {
  try { await fs.access(file); return true; }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false; throw error; }
}
async function assertContained(root: string, file: string): Promise<void> {
  const [realRoot, realFile] = await Promise.all([fs.realpath(root), fs.realpath(file)]);
  const relative = path.relative(realRoot, realFile);
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('Storage path escapes trace root');
  }
}
async function containedDirectory(root: string, dir: string): Promise<void> {
  await fs.mkdir(root, { recursive: true });
  // Check each existing ancestor before mkdir can follow an escaping symlink.
  let current = path.resolve(root);
  for (const part of path.relative(current, dir).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (await exists(current)) await assertContained(root, current);
    else {
      try { await fs.mkdir(current); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error; }
      await assertContained(root, current);
    }
  }
  await assertContained(root, dir);
}
async function readJson(file: string, maxBytes: number): Promise<unknown> {
  const handle = await fs.open(file, constants.O_RDONLY | constants.O_NONBLOCK);
  try {
    const metadata = await handle.stat();
    if (!metadata.isFile() || metadata.size > maxBytes) throw new Error('Invalid or oversized trace file');
    const chunks: Buffer[] = [];
    let total = 0;
    while (true) {
      const block = Buffer.allocUnsafe(Math.min(64 * 1024, maxBytes + 1 - total));
      const { bytesRead } = await handle.read(block, 0, block.length, null);
      if (!bytesRead) break;
      total += bytesRead;
      if (total > maxBytes) throw new Error('Trace file exceeds read budget');
      chunks.push(block.subarray(0, bytesRead));
    }
    return JSON.parse(Buffer.concat(chunks, total).toString('utf-8'));
  } finally { await handle.close(); }
}
async function atomicJson(file: string, content: string): Promise<void> {
  const temp = `${file}.${randomUUID()}.tmp`;
  let handle;
  try {
    handle = await fs.open(temp, 'wx', 0o600);
    await handle.writeFile(content, 'utf-8');
    await handle.sync();
    await handle.close(); handle = undefined;
    await fs.rename(temp, file);
  } finally {
    await handle?.close();
    await fs.rm(temp, { force: true });
  }
}
function safeRawPath(trace: UnifiedTrace, rawRoot: string): string {
  validateId(trace.trace_id); validateSource(trace.source);
  const basename = path.basename(trace.full_data_path || '');
  const escapedId = trace.trace_id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  if (!new RegExp(`^${escapedId}(?:\\.[a-f0-9]{64})?\\.json$`).test(basename)) {
    throw new Error('Invalid raw revision locator');
  }
  return path.join(rawRoot, trace.source, basename);
}
function indexEntry(trace: UnifiedTrace): TraceIndexEntry {
  return { trace_id: trace.trace_id, source: trace.source, timestamp: trace.timestamp,
    severity: trace.severity, category: trace.category, operation: trace.operation,
    summary_preview: trace.summary.description.substring(0, 100),
    has_error: trace.severity === 'error' || trace.severity === 'fatal',
    incident_ids: trace.incident_ids || [], has_full_data: trace.has_full_data,
    full_data_path: trace.full_data_path };
}
function addEntry(index: TraceIndex, entry: TraceIndexEntry): void {
  index.traces[entry.trace_id] = entry;
  index.total_traces++;
  for (const [map, key] of [[index.by_source, entry.source], [index.by_category, entry.category],
      [index.by_severity, entry.severity], [index.by_date, new Date(entry.timestamp).toISOString().slice(0, 10)],
      ...entry.incident_ids.map(id => [index.by_incident, id])] as [Record<string, string[]>, string][]) {
    if (!Object.prototype.hasOwnProperty.call(map, key)) Object.defineProperty(map, key, {value: [], enumerable: true});
    map[key].push(entry.trace_id);
  }
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
export async function createCollection(
  name: string,
  traceIds: string[],
  incident_id?: string,
  config?: MemoryConfig
): Promise<TraceCollection> {
  const paths = getTracePaths(config);

  // Load traces to generate summary
  const index = await loadTraceIndex(config);
  const traces = traceIds.map(id => {
    validateId(id);
    return index.traces[id] ? reconstructTraceFromIndex(index.traces[id]) : null;
  });
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
  await containedDirectory(paths.traces, paths.collections);
  const filepath = path.join(paths.collections, `${collection.collection_id}.json`);
  const serialized = JSON.stringify(collection, null, 2);
  if (Buffer.byteLength(serialized) > 1024 * 1024) throw new Error('Collection exceeds 1 MiB');
  await atomicJson(filepath, serialized);

  return collection;
}

/**
 * Load a collection by ID
 */
export async function loadCollection(
  collection_id: string,
  config?: MemoryConfig
): Promise<TraceCollection | null> {
  validateId(collection_id);
  const paths = getTracePaths(config);
  const filepath = path.join(paths.collections, `${collection_id}.json`);

  try {
    await assertContained(paths.traces, filepath);
    return await readJson(filepath, 1024 * 1024) as TraceCollection;
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
export async function correlateTraceToIncident(
  trace_id: string,
  incident_id: string,
  config?: MemoryConfig
): Promise<void> {
  validateId(trace_id);
  validateId(incident_id);
  const index = await loadTraceIndex(config);
  if (!index.traces[trace_id]) return;
  const paths = getTracePaths(config);
  const directory = path.join(paths.traces, 'correlations', trace_id);
  await containedDirectory(paths.traces, directory);
  await atomicJson(path.join(directory, `${incident_id}.json`), JSON.stringify({trace_id, incident_id}));
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
