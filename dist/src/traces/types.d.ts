/**
 * Trace System - Type Definitions
 *
 * Unified types for trace ingestion from multiple sources:
 * - OpenTelemetry (OTLP JSON)
 * - Sentry (Error events + breadcrumbs)
 * - Langchain (LangSmith runs)
 * - Browser (Chrome DevTools, Playwright, Console)
 */
/**
 * Trace source enumeration
 */
export type TraceSource = 'opentelemetry' | 'sentry' | 'langchain' | 'chrome-devtools' | 'playwright' | 'browser-console' | 'internal';
/**
 * Trace severity levels (normalized across sources)
 */
export type TraceSeverity = 'debug' | 'info' | 'warning' | 'error' | 'fatal';
/**
 * Trace categories for filtering
 */
export type TraceCategory = 'http-request' | 'http-response' | 'database-query' | 'cache-operation' | 'llm-call' | 'llm-chain' | 'ui-interaction' | 'ui-render' | 'console-log' | 'console-error' | 'network-request' | 'performance-metric' | 'custom';
/**
 * Unified Trace - The canonical format stored in memory
 *
 * All trace sources are transformed into this format for
 * consistent storage, retrieval, and context injection.
 */
export interface UnifiedTrace {
    trace_id: string;
    source: TraceSource;
    external_id?: string;
    timestamp: number;
    duration_ms?: number;
    severity: TraceSeverity;
    category: TraceCategory;
    operation: string;
    summary: TraceSummary;
    incident_ids?: string[];
    session_id?: string;
    has_full_data: boolean;
    full_data_path?: string;
    relevance_score?: number;
    tokens_estimated: number;
}
/**
 * Token-efficient summary for LLM context injection
 *
 * Maximum ~100 tokens per trace summary.
 */
export interface TraceSummary {
    description: string;
    highlights: TraceHighlight[];
    error?: TraceErrorSummary;
    performance?: TracePerformanceSummary;
}
/**
 * Key-value highlight for trace summary
 */
export interface TraceHighlight {
    label: string;
    value: string;
}
/**
 * Error summary for trace
 */
export interface TraceErrorSummary {
    type: string;
    message: string;
    stack_preview?: string;
}
/**
 * Performance summary for trace
 */
export interface TracePerformanceSummary {
    duration_ms: number;
    is_slow: boolean;
    bottleneck?: string;
}
/**
 * Trace collection - Groups related traces
 */
export interface TraceCollection {
    collection_id: string;
    name: string;
    traces: string[];
    created_at: number;
    incident_id?: string;
    summary: CollectionSummary;
}
/**
 * Collection summary statistics
 */
export interface CollectionSummary {
    total_traces: number;
    by_severity: Record<TraceSeverity, number>;
    by_category: Record<string, number>;
    time_range: {
        start: number;
        end: number;
    };
    total_errors: number;
    slowest_operation?: string;
}
/**
 * Trace index for fast lookup
 */
export interface TraceIndex {
    version: string;
    last_updated: number;
    total_traces: number;
    traces: Record<string, TraceIndexEntry>;
    by_source: Record<TraceSource, string[]>;
    by_category: Record<TraceCategory, string[]>;
    by_severity: Record<TraceSeverity, string[]>;
    by_date: Record<string, string[]>;
    by_incident: Record<string, string[]>;
}
/**
 * Index entry for a single trace
 */
export interface TraceIndexEntry {
    trace_id: string;
    source: TraceSource;
    timestamp: number;
    severity: TraceSeverity;
    category: TraceCategory;
    operation: string;
    summary_preview: string;
    has_error: boolean;
    incident_ids: string[];
}
/**
 * Trace adapter interface
 *
 * Each trace source has a dedicated adapter that transforms
 * source-specific data into the unified format.
 */
export interface TraceAdapter<TInput> {
    /**
     * Source identifier
     */
    readonly source: TraceSource;
    /**
     * Parse raw input into unified traces
     */
    parse(input: TInput): Promise<UnifiedTrace[]>;
    /**
     * Validate input format
     */
    validate(input: unknown): input is TInput;
    /**
     * Extract summary from raw trace (token-efficient)
     */
    summarize(trace: UnifiedTrace, rawData: TInput): TraceSummary;
    /**
     * Store full data for lazy loading
     */
    storeFullData(trace: UnifiedTrace, rawData: TInput): Promise<string>;
}
/**
 * OpenTelemetry OTLP JSON format
 */
export interface OTLPTrace {
    resourceSpans: Array<{
        resource: {
            attributes: Array<{
                key: string;
                value: OTLPValue;
            }>;
        };
        scopeSpans: Array<{
            scope: {
                name: string;
                version?: string;
            };
            spans: OTLPSpan[];
        }>;
    }>;
}
export interface OTLPSpan {
    traceId: string;
    spanId: string;
    parentSpanId?: string;
    name: string;
    kind: number;
    startTimeUnixNano: string;
    endTimeUnixNano: string;
    attributes?: Array<{
        key: string;
        value: OTLPValue;
    }>;
    status?: {
        code: number;
        message?: string;
    };
    events?: Array<{
        name: string;
        timeUnixNano: string;
        attributes?: Array<{
            key: string;
            value: OTLPValue;
        }>;
    }>;
}
export interface OTLPValue {
    stringValue?: string;
    intValue?: number;
    boolValue?: boolean;
    doubleValue?: number;
    arrayValue?: {
        values: OTLPValue[];
    };
}
/**
 * Sentry event format
 */
export interface SentryEvent {
    event_id: string;
    timestamp: string;
    platform: string;
    level: 'fatal' | 'error' | 'warning' | 'info' | 'debug';
    logger?: string;
    transaction?: string;
    release?: string;
    environment?: string;
    exception?: {
        values: Array<{
            type: string;
            value: string;
            stacktrace?: {
                frames: Array<{
                    filename: string;
                    function: string;
                    lineno?: number;
                    colno?: number;
                    context_line?: string;
                }>;
            };
        }>;
    };
    breadcrumbs?: {
        values: Array<{
            type: string;
            category: string;
            message?: string;
            timestamp: string;
            data?: Record<string, unknown>;
        }>;
    };
    tags?: Record<string, string>;
    contexts?: Record<string, unknown>;
}
/**
 * LangSmith run format
 */
export interface LangSmithRun {
    id: string;
    name: string;
    run_type: 'llm' | 'chain' | 'tool' | 'retriever' | 'embedding';
    start_time: string;
    end_time?: string;
    status: 'success' | 'error' | 'pending';
    dotted_order: string;
    parent_run_id?: string;
    inputs?: Record<string, unknown>;
    outputs?: Record<string, unknown>;
    error?: string;
    extra?: {
        metadata?: Record<string, unknown>;
        runtime?: {
            model?: string;
            provider?: string;
        };
    };
    token_usage?: {
        prompt_tokens?: number;
        completion_tokens?: number;
        total_tokens?: number;
    };
}
/**
 * Chrome DevTools Performance trace
 */
export interface ChromeTrace {
    traceEvents: ChromeTraceEvent[];
    metadata?: Record<string, unknown>;
}
export interface ChromeTraceEvent {
    pid: number;
    tid: number;
    ts: number;
    dur?: number;
    ph: string;
    name: string;
    cat?: string;
    args?: Record<string, unknown>;
}
/**
 * Playwright trace format
 */
export interface PlaywrightTrace {
    actions: PlaywrightAction[];
    network: HAREntry[];
    console: ConsoleMessage[];
}
export interface PlaywrightAction {
    name: string;
    startTime: number;
    endTime: number;
    error?: string;
    snapshots?: string[];
}
export interface HAREntry {
    startedDateTime: string;
    request: {
        method: string;
        url: string;
    };
    response: {
        status: number;
        statusText: string;
    };
    time: number;
}
export interface ConsoleMessage {
    type: 'log' | 'warn' | 'error' | 'info' | 'debug';
    text: string;
    timestamp: number;
}
/**
 * Correlation configuration
 */
export interface CorrelationConfig {
    auto_correlate: boolean;
    time_window_minutes: number;
    similarity_threshold: number;
}
/**
 * Trace correlation result
 */
export interface TraceCorrelation {
    trace_id: string;
    incident_id: string;
    correlation_type: 'manual' | 'automatic' | 'temporal' | 'keyword';
    confidence: number;
    created_at: number;
}
/**
 * Summarization configuration
 */
export interface SummarizationConfig {
    max_tokens_per_trace: number;
    max_tokens_total: number;
    prioritize_errors: boolean;
    include_performance: boolean;
    time_window_minutes?: number;
}
/**
 * Summarized output section
 */
export interface SummarySection {
    text: string;
    tokens: number;
}
//# sourceMappingURL=types.d.ts.map