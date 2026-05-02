/**
 * Trace System - Entry Point
 *
 * Exports all trace functionality.
 */

// Types
export type {
  TraceSource,
  TraceSeverity,
  TraceCategory,
  UnifiedTrace,
  TraceSummary,
  TraceHighlight,
  TraceErrorSummary,
  TracePerformanceSummary,
  TraceCollection,
  CollectionSummary,
  TraceIndex,
  TraceIndexEntry,
  TraceAdapter,
  SummarizationConfig,
  CorrelationConfig,
  TraceCorrelation,
  // Source-specific types
  OTLPTrace,
  OTLPSpan,
  SentryEvent,
  LangSmithRun,
  ChromeTrace,
  PlaywrightTrace,
} from './types';

// Storage
export {
  generateTraceId,
  generateCollectionId,
  storeTrace,
  loadTraceSummary,
  loadFullTrace,
  loadAllTraces,
  loadTracesBySource,
  loadTracesByCategory,
  loadTracesBySeverity,
  loadTracesForIncident,
  loadTraceIndex,
  createCollection,
  loadCollection,
  correlateTraceToIncident,
  getTraceStats,
} from './storage';

// Summarizer
export {
  TraceSummarizer,
  createTraceSummary,
  summarizeTracesCompact,
  filterByTimeWindow,
  groupByCategory,
  getTopErrors,
  getSlowestOperations,
} from './summarizer';

// Adapters
export {
  OpenTelemetryAdapter,
  SentryAdapter,
  LangchainAdapter,
  BrowserTraceAdapter,
  getAdapter,
  autoParseTraces,
} from './adapters';
