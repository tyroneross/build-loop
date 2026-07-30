/**
 * OpenTelemetry Adapter
 *
 * Parses OTLP JSON format traces into unified format.
 */
import type { TraceAdapter, TraceSource, UnifiedTrace, TraceSummary, OTLPTrace } from '../types';
export declare class OpenTelemetryAdapter implements TraceAdapter<OTLPTrace> {
    readonly source: TraceSource;
    /**
     * Validate if input is OTLP format
     */
    validate(input: unknown): input is OTLPTrace;
    /**
     * Parse OTLP trace into unified traces
     */
    parse(input: OTLPTrace): Promise<UnifiedTrace[]>;
    /**
     * Convert single OTLP span to unified trace
     */
    private convertSpan;
    /**
     * Create summary for span
     */
    summarize(trace: UnifiedTrace, rawData: OTLPTrace): TraceSummary;
    /**
     * Summarize a single span
     */
    private summarizeSpan;
    /**
     * Store full data for lazy loading
     */
    storeFullData(trace: UnifiedTrace, rawData: OTLPTrace): Promise<string>;
    /**
     * Infer category from span attributes
     */
    private inferCategory;
    /**
     * Extract attribute value from OTLP attributes
     */
    private extractAttribute;
    /**
     * Truncate URL for display
     */
    private truncateUrl;
}
//# sourceMappingURL=opentelemetry.d.ts.map