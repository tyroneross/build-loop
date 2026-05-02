/**
 * Trace Storage Module
 *
 * Handles reading/writing traces to filesystem with indexing.
 * Supports lazy loading of full trace data.
 */
import type { UnifiedTrace, TraceIndex, TraceSource, TraceCategory, TraceSeverity, TraceCollection } from './types';
import type { MemoryConfig } from '../types';
/**
 * Generate a unique trace ID
 */
export declare function generateTraceId(): string;
/**
 * Generate a collection ID
 */
export declare function generateCollectionId(): string;
/**
 * Store a trace with indexing
 */
export declare function storeTrace(trace: UnifiedTrace, rawData?: unknown, config?: MemoryConfig): Promise<{
    trace_id: string;
    file_path: string;
}>;
/**
 * Load trace summary by ID
 */
export declare function loadTraceSummary(trace_id: string, config?: MemoryConfig): Promise<UnifiedTrace | null>;
/**
 * Load full trace data (lazy loading)
 */
export declare function loadFullTrace(trace_id: string, config?: MemoryConfig): Promise<unknown | null>;
/**
 * Load all traces (summaries only)
 */
export declare function loadAllTraces(config?: MemoryConfig): Promise<UnifiedTrace[]>;
/**
 * Load traces by source
 */
export declare function loadTracesBySource(source: TraceSource, config?: MemoryConfig): Promise<UnifiedTrace[]>;
/**
 * Load traces by category
 */
export declare function loadTracesByCategory(category: TraceCategory, config?: MemoryConfig): Promise<UnifiedTrace[]>;
/**
 * Load traces by severity
 */
export declare function loadTracesBySeverity(severity: TraceSeverity, config?: MemoryConfig): Promise<UnifiedTrace[]>;
/**
 * Load traces correlated to an incident
 */
export declare function loadTracesForIncident(incident_id: string, config?: MemoryConfig): Promise<UnifiedTrace[]>;
/**
 * Load or initialize trace index
 */
export declare function loadTraceIndex(config?: MemoryConfig): Promise<TraceIndex>;
/**
 * Create a trace collection
 */
export declare function createCollection(name: string, traceIds: string[], incident_id?: string, config?: MemoryConfig): Promise<TraceCollection>;
/**
 * Load a collection by ID
 */
export declare function loadCollection(collection_id: string, config?: MemoryConfig): Promise<TraceCollection | null>;
/**
 * Correlate a trace to an incident
 */
export declare function correlateTraceToIncident(trace_id: string, incident_id: string, config?: MemoryConfig): Promise<void>;
/**
 * Get trace statistics
 */
export declare function getTraceStats(config?: MemoryConfig): Promise<{
    total_traces: number;
    by_source: Record<string, number>;
    by_severity: Record<string, number>;
    oldest_trace: number;
    newest_trace: number;
}>;
//# sourceMappingURL=storage.d.ts.map