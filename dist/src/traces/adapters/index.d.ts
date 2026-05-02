/**
 * Trace Adapters - Entry Point
 *
 * Exports all trace adapters for different sources.
 */
export { OpenTelemetryAdapter } from './opentelemetry';
export { SentryAdapter } from './sentry';
export { LangchainAdapter } from './langchain';
export { BrowserTraceAdapter } from './browser';
import type { TraceSource, UnifiedTrace } from '../types';
import { OpenTelemetryAdapter } from './opentelemetry';
import { SentryAdapter } from './sentry';
import { LangchainAdapter } from './langchain';
import { BrowserTraceAdapter } from './browser';
/**
 * Get adapter for a specific source
 */
export declare function getAdapter(source: TraceSource): OpenTelemetryAdapter | SentryAdapter | LangchainAdapter | BrowserTraceAdapter;
/**
 * Auto-detect source and parse traces
 */
export declare function autoParseTraces(input: unknown): Promise<UnifiedTrace[]>;
//# sourceMappingURL=index.d.ts.map