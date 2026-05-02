/**
 * Langchain Adapter
 *
 * Parses LangSmith run format into unified traces.
 */
import type { TraceAdapter, TraceSource, UnifiedTrace, TraceSummary, LangSmithRun } from '../types';
export declare class LangchainAdapter implements TraceAdapter<LangSmithRun[]> {
    readonly source: TraceSource;
    /**
     * Validate if input is LangSmith run format
     */
    validate(input: unknown): input is LangSmithRun[];
    /**
     * Parse LangSmith runs into unified traces
     */
    parse(input: LangSmithRun[]): Promise<UnifiedTrace[]>;
    /**
     * Convert single LangSmith run to unified trace
     */
    private convertRun;
    /**
     * Create summary from trace
     */
    summarize(trace: UnifiedTrace, rawData: LangSmithRun[]): TraceSummary;
    /**
     * Build run summary
     */
    private buildRunSummary;
    /**
     * Store full data for lazy loading
     */
    storeFullData(trace: UnifiedTrace, rawData: LangSmithRun[]): Promise<string>;
    /**
     * Map run type to trace category
     */
    private mapRunType;
    /**
     * Identify performance bottleneck
     */
    private identifyBottleneck;
}
//# sourceMappingURL=langchain.d.ts.map