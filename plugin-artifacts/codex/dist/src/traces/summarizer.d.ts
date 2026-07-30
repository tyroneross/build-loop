/**
 * Trace Summarizer
 *
 * Token-budget-aware summarization of traces for LLM context injection.
 * Prioritizes errors and performance issues within token limits.
 */
import type { UnifiedTrace, TraceSummary, SummarizationConfig } from './types';
/**
 * TraceSummarizer - Main class for trace summarization
 */
export declare class TraceSummarizer {
    private config;
    constructor(config?: Partial<SummarizationConfig>);
    /**
     * Generate context-efficient summary for LLM injection
     *
     * Structure:
     * 1. Errors (highest priority)
     * 2. Performance issues
     * 3. Timeline of operations
     */
    summarizeForContext(traces: UnifiedTrace[]): string;
    /**
     * Build error summary section
     */
    private buildErrorSummary;
    /**
     * Build performance summary section
     */
    private buildPerformanceSummary;
    /**
     * Build timeline summary section
     */
    private buildTimelineSummary;
    /**
     * Sort traces by relevance for summarization
     */
    private sortByRelevance;
    /**
     * Estimate tokens for text
     */
    estimateTokens(text: string): number;
}
/**
 * Create a compact trace summary for single trace
 */
export declare function createTraceSummary(operation: string, description: string, options?: {
    error?: {
        type: string;
        message: string;
        stack_preview?: string;
    };
    performance?: {
        duration_ms: number;
        is_slow: boolean;
        bottleneck?: string;
    };
    highlights?: Array<{
        label: string;
        value: string;
    }>;
}): TraceSummary;
/**
 * Summarize multiple traces into a single paragraph
 */
export declare function summarizeTracesCompact(traces: UnifiedTrace[]): string;
/**
 * Filter traces by time window
 */
export declare function filterByTimeWindow(traces: UnifiedTrace[], windowMinutes: number): UnifiedTrace[];
/**
 * Group traces by category
 */
export declare function groupByCategory(traces: UnifiedTrace[]): Record<string, UnifiedTrace[]>;
/**
 * Get top errors from traces
 */
export declare function getTopErrors(traces: UnifiedTrace[], limit?: number): UnifiedTrace[];
/**
 * Get slowest operations from traces
 */
export declare function getSlowestOperations(traces: UnifiedTrace[], limit?: number): UnifiedTrace[];
//# sourceMappingURL=summarizer.d.ts.map