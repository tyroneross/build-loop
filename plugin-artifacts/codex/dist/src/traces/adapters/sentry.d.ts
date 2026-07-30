/**
 * Sentry Adapter
 *
 * Parses Sentry error events and breadcrumbs into unified format.
 */
import type { TraceAdapter, TraceSource, UnifiedTrace, TraceSummary, SentryEvent } from '../types';
export declare class SentryAdapter implements TraceAdapter<SentryEvent> {
    readonly source: TraceSource;
    /**
     * Validate if input is Sentry event format
     */
    validate(input: unknown): input is SentryEvent;
    /**
     * Parse Sentry event into unified traces
     */
    parse(input: SentryEvent): Promise<UnifiedTrace[]>;
    /**
     * Convert Sentry event to unified trace
     */
    private convertEvent;
    /**
     * Convert breadcrumb to unified trace
     */
    private convertBreadcrumb;
    /**
     * Create summary from trace
     */
    summarize(trace: UnifiedTrace, rawData: SentryEvent): TraceSummary;
    /**
     * Build event summary
     */
    private buildEventSummary;
    /**
     * Build breadcrumb highlights
     */
    private buildBreadcrumbHighlights;
    /**
     * Extract stack preview from stacktrace
     */
    private extractStackPreview;
    /**
     * Store full data for lazy loading
     */
    storeFullData(trace: UnifiedTrace, rawData: SentryEvent): Promise<string>;
    /**
     * Map Sentry level to trace severity
     */
    private mapSeverity;
    /**
     * Map breadcrumb type to severity
     */
    private mapBreadcrumbSeverity;
    /**
     * Map breadcrumb category to trace category
     */
    private mapBreadcrumbCategory;
}
//# sourceMappingURL=sentry.d.ts.map