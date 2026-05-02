/**
 * Browser Trace Adapter
 *
 * Parses Chrome DevTools Performance traces, Playwright traces,
 * and browser console logs into unified format.
 */
import type { TraceAdapter, TraceSource, UnifiedTrace, TraceSummary, ChromeTrace, PlaywrightTrace } from '../types';
type BrowserInput = ChromeTrace | PlaywrightTrace;
export declare class BrowserTraceAdapter implements TraceAdapter<BrowserInput> {
    readonly source: TraceSource;
    /**
     * Validate if input is a browser trace format
     */
    validate(input: unknown): input is BrowserInput;
    /**
     * Parse browser traces into unified format
     */
    parse(input: BrowserInput): Promise<UnifiedTrace[]>;
    /**
     * Create summary from trace
     */
    summarize(trace: UnifiedTrace, rawData: BrowserInput): TraceSummary;
    /**
     * Store full data for lazy loading
     */
    storeFullData(trace: UnifiedTrace, rawData: BrowserInput): Promise<string>;
    /**
     * Parse Chrome DevTools performance trace
     */
    private parseChrome;
    /**
     * Summarize a group of Chrome events
     */
    private summarizeChromeGroup;
    /**
     * Check if Chrome category is relevant
     */
    private isRelevantCategory;
    /**
     * Group Chrome events by category
     */
    private groupByCategory;
    /**
     * Map Chrome category to trace category
     */
    private mapChromeCategory;
    /**
     * Parse Playwright trace
     */
    private parsePlaywright;
    /**
     * Convert Playwright action to trace
     */
    private convertPlaywrightAction;
    /**
     * Convert network error to trace
     */
    private convertNetworkError;
    /**
     * Convert console error to trace
     */
    private convertConsoleError;
    /**
     * Truncate URL for display
     */
    private truncateUrl;
}
export {};
//# sourceMappingURL=browser.d.ts.map