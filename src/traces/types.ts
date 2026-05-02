/**
 * Trace System - Type Definitions
 *
 * Unified types for trace ingestion from multiple sources:
 * - OpenTelemetry (OTLP JSON)
 * - Sentry (Error events + breadcrumbs)
 * - Langchain (LangSmith runs)
 * - Browser (Chrome DevTools, Playwright, Console)
 */

// ============================================================================
// CORE TRACE TYPES
// ============================================================================

/**
 * Trace source enumeration
 */
export type TraceSource =
  | 'opentelemetry'
  | 'sentry'
  | 'langchain'
  | 'chrome-devtools'
  | 'playwright'
  | 'browser-console'
  | 'internal';

/**
 * Trace severity levels (normalized across sources)
 */
export type TraceSeverity = 'debug' | 'info' | 'warning' | 'error' | 'fatal';

/**
 * Trace categories for filtering
 */
export type TraceCategory =
  | 'http-request'
  | 'http-response'
  | 'database-query'
  | 'cache-operation'
  | 'llm-call'
  | 'llm-chain'
  | 'ui-interaction'
  | 'ui-render'
  | 'console-log'
  | 'console-error'
  | 'network-request'
  | 'performance-metric'
  | 'custom';

/**
 * Unified Trace - The canonical format stored in memory
 *
 * All trace sources are transformed into this format for
 * consistent storage, retrieval, and context injection.
 */
export interface UnifiedTrace {
  // Identification
  trace_id: string;              // TRC_YYYYMMDD_HHMMSS_random
  source: TraceSource;           // Origin of the trace
  external_id?: string;          // Original ID from source system

  // Temporal
  timestamp: number;             // Unix timestamp (start)
  duration_ms?: number;          // Duration in milliseconds

  // Classification
  severity: TraceSeverity;
  category: TraceCategory;
  operation: string;             // Human-readable operation name

  // Summary (token-efficient representation)
  summary: TraceSummary;

  // Correlation
  incident_ids?: string[];       // Linked incident IDs
  session_id?: string;           // Debugging session context

  // Lazy loading support
  has_full_data: boolean;        // Indicates full data available
  full_data_path?: string;       // Path to raw trace file

  // Quality & Relevance
  relevance_score?: number;      // 0-1, how relevant to debugging
  tokens_estimated: number;      // Estimated tokens for summary
}

/**
 * Token-efficient summary for LLM context injection
 *
 * Maximum ~100 tokens per trace summary.
 */
export interface TraceSummary {
  // One-line description
  description: string;           // Max 200 chars

  // Key-value highlights (3-5 items max)
  highlights: TraceHighlight[];

  // Error info if applicable
  error?: TraceErrorSummary;

  // Performance info if applicable
  performance?: TracePerformanceSummary;
}

/**
 * Key-value highlight for trace summary
 */
export interface TraceHighlight {
  label: string;                 // e.g., "endpoint", "model", "query"
  value: string;                 // e.g., "/api/search", "gpt-4", "SELECT..."
}

/**
 * Error summary for trace
 */
export interface TraceErrorSummary {
  type: string;                  // Error type/class
  message: string;               // Error message (truncated)
  stack_preview?: string;        // First 2-3 stack frames
}

/**
 * Performance summary for trace
 */
export interface TracePerformanceSummary {
  duration_ms: number;
  is_slow: boolean;              // Exceeded expected threshold
  bottleneck?: string;           // Identified bottleneck
}

// ============================================================================
// TRACE COLLECTION
// ============================================================================

/**
 * Trace collection - Groups related traces
 */
export interface TraceCollection {
  collection_id: string;         // COL_timestamp_random
  name: string;                  // e.g., "Search feature debugging"
  traces: string[];              // Trace IDs
  created_at: number;
  incident_id?: string;          // Primary incident correlation
  summary: CollectionSummary;
}

/**
 * Collection summary statistics
 */
export interface CollectionSummary {
  total_traces: number;
  by_severity: Record<TraceSeverity, number>;
  by_category: Record<string, number>;
  time_range: { start: number; end: number };
  total_errors: number;
  slowest_operation?: string;
}

// ============================================================================
// TRACE INDEX
// ============================================================================

/**
 * Trace index for fast lookup
 */
export interface TraceIndex {
  version: string;                    // Schema version
  last_updated: number;               // Unix timestamp
  total_traces: number;

  // Quick lookup by ID
  traces: Record<string, TraceIndexEntry>;

  // Indexes for efficient filtering
  by_source: Record<TraceSource, string[]>;
  by_category: Record<TraceCategory, string[]>;
  by_severity: Record<TraceSeverity, string[]>;
  by_date: Record<string, string[]>;  // YYYY-MM-DD -> trace_ids

  // Correlation index
  by_incident: Record<string, string[]>;  // incident_id -> trace_ids
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
  summary_preview: string;           // First 100 chars of description
  has_error: boolean;
  incident_ids: string[];
}

// ============================================================================
// ADAPTER INTERFACE
// ============================================================================

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

// ============================================================================
// OPENTELEMETRY TYPES
// ============================================================================

/**
 * OpenTelemetry OTLP JSON format
 */
export interface OTLPTrace {
  resourceSpans: Array<{
    resource: {
      attributes: Array<{ key: string; value: OTLPValue }>;
    };
    scopeSpans: Array<{
      scope: { name: string; version?: string };
      spans: OTLPSpan[];
    }>;
  }>;
}

export interface OTLPSpan {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: number;  // 1=client, 2=server, 3=internal
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  attributes?: Array<{ key: string; value: OTLPValue }>;
  status?: { code: number; message?: string };
  events?: Array<{
    name: string;
    timeUnixNano: string;
    attributes?: Array<{ key: string; value: OTLPValue }>;
  }>;
}

export interface OTLPValue {
  stringValue?: string;
  intValue?: number;
  boolValue?: boolean;
  doubleValue?: number;
  arrayValue?: { values: OTLPValue[] };
}

// ============================================================================
// SENTRY TYPES
// ============================================================================

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

// ============================================================================
// LANGCHAIN TYPES
// ============================================================================

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
    runtime?: { model?: string; provider?: string };
  };
  token_usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

// ============================================================================
// BROWSER TRACE TYPES
// ============================================================================

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
  ts: number;      // Microseconds
  dur?: number;    // Microseconds
  ph: string;      // Phase: X=complete, B=begin, E=end, I=instant
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
  request: { method: string; url: string };
  response: { status: number; statusText: string };
  time: number;
}

export interface ConsoleMessage {
  type: 'log' | 'warn' | 'error' | 'info' | 'debug';
  text: string;
  timestamp: number;
}

// ============================================================================
// CORRELATION TYPES
// ============================================================================

/**
 * Correlation configuration
 */
export interface CorrelationConfig {
  auto_correlate: boolean;           // Auto-link traces to active incidents
  time_window_minutes: number;       // Look for traces within this window
  similarity_threshold: number;      // Min similarity for auto-correlation
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

// ============================================================================
// SUMMARIZATION TYPES
// ============================================================================

/**
 * Summarization configuration
 */
export interface SummarizationConfig {
  max_tokens_per_trace: number;      // Default: 100
  max_tokens_total: number;          // Default: 2000
  prioritize_errors: boolean;        // Default: true
  include_performance: boolean;      // Default: true
  time_window_minutes?: number;      // Filter by recency
}

/**
 * Summarized output section
 */
export interface SummarySection {
  text: string;
  tokens: number;
}
