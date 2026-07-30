/**
 * Trace System - Entry Point
 *
 * Exports all trace functionality.
 */
export type { TraceSource, TraceSeverity, TraceCategory, UnifiedTrace, TraceSummary, TraceHighlight, TraceErrorSummary, TracePerformanceSummary, TraceCollection, CollectionSummary, TraceIndex, TraceIndexEntry, TraceAdapter, SummarizationConfig, CorrelationConfig, TraceCorrelation, OTLPTrace, OTLPSpan, SentryEvent, LangSmithRun, ChromeTrace, PlaywrightTrace, } from './types';
export { generateTraceId, generateCollectionId, storeTrace, loadTraceSummary, loadFullTrace, loadAllTraces, loadTracesBySource, loadTracesByCategory, loadTracesBySeverity, loadTracesForIncident, loadTraceIndex, createCollection, loadCollection, correlateTraceToIncident, getTraceStats, } from './storage';
export { TraceSummarizer, createTraceSummary, summarizeTracesCompact, filterByTimeWindow, groupByCategory, getTopErrors, getSlowestOperations, } from './summarizer';
export { OpenTelemetryAdapter, SentryAdapter, LangchainAdapter, BrowserTraceAdapter, getAdapter, autoParseTraces, } from './adapters';
//# sourceMappingURL=index.d.ts.map