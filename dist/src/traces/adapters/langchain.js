"use strict";
/**
 * Langchain Adapter
 *
 * Parses LangSmith run format into unified traces.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.LangchainAdapter = void 0;
const storage_1 = require("../storage");
const summarizer_1 = require("../summarizer");
class LangchainAdapter {
    constructor() {
        this.source = 'langchain';
    }
    /**
     * Validate if input is LangSmith run format
     */
    validate(input) {
        if (!Array.isArray(input))
            return false;
        if (input.length === 0)
            return true; // Empty array is valid
        const first = input[0];
        return (typeof first === 'object' &&
            first !== null &&
            typeof first.id === 'string' &&
            typeof first.name === 'string' &&
            typeof first.run_type === 'string');
    }
    /**
     * Parse LangSmith runs into unified traces
     */
    async parse(input) {
        return input.map((run) => this.convertRun(run));
    }
    /**
     * Convert single LangSmith run to unified trace
     */
    convertRun(run) {
        const startTime = new Date(run.start_time).getTime();
        const endTime = run.end_time ? new Date(run.end_time).getTime() : undefined;
        const durationMs = endTime ? endTime - startTime : undefined;
        return {
            trace_id: (0, storage_1.generateTraceId)(),
            source: 'langchain',
            external_id: run.id,
            timestamp: startTime,
            duration_ms: durationMs,
            severity: run.status === 'error' ? 'error' : 'info',
            category: this.mapRunType(run.run_type),
            operation: run.name,
            summary: this.buildRunSummary(run, durationMs),
            has_full_data: true,
            tokens_estimated: 60,
        };
    }
    /**
     * Create summary from trace
     */
    summarize(trace, rawData) {
        return trace.summary;
    }
    /**
     * Build run summary
     */
    buildRunSummary(run, durationMs) {
        const highlights = [];
        // Add model info
        if (run.extra?.runtime?.model) {
            highlights.push({ label: 'model', value: run.extra.runtime.model });
        }
        if (run.extra?.runtime?.provider) {
            highlights.push({ label: 'provider', value: run.extra.runtime.provider });
        }
        // Add token usage
        if (run.token_usage?.total_tokens) {
            highlights.push({
                label: 'tokens',
                value: String(run.token_usage.total_tokens),
            });
        }
        // Add run type
        highlights.push({ label: 'type', value: run.run_type });
        // Build description
        let description = `${run.run_type}: ${run.name}`;
        if (run.extra?.runtime?.model) {
            description = `${run.run_type}[${run.extra.runtime.model}]: ${run.name}`;
        }
        return (0, summarizer_1.createTraceSummary)(run.name, description, {
            highlights: highlights.slice(0, 5),
            error: run.error
                ? {
                    type: 'LangChainError',
                    message: run.error.substring(0, 200),
                }
                : undefined,
            performance: durationMs
                ? {
                    duration_ms: durationMs,
                    is_slow: durationMs > 10000, // > 10 seconds is slow for LLM calls
                    bottleneck: this.identifyBottleneck(run, durationMs),
                }
                : undefined,
        });
    }
    /**
     * Store full data for lazy loading
     */
    async storeFullData(trace, rawData) {
        const result = await (0, storage_1.storeTrace)(trace, rawData);
        return result.file_path;
    }
    /**
     * Map run type to trace category
     */
    mapRunType(runType) {
        switch (runType) {
            case 'llm':
                return 'llm-call';
            case 'chain':
                return 'llm-chain';
            case 'tool':
                return 'custom';
            case 'retriever':
                return 'database-query';
            case 'embedding':
                return 'llm-call';
            default:
                return 'custom';
        }
    }
    /**
     * Identify performance bottleneck
     */
    identifyBottleneck(run, durationMs) {
        if (durationMs > 30000) {
            return 'Extremely slow LLM response';
        }
        if (durationMs > 15000 && run.run_type === 'llm') {
            return 'Slow LLM inference';
        }
        if (run.token_usage && run.token_usage.total_tokens && run.token_usage.total_tokens > 10000) {
            return 'High token usage';
        }
        return undefined;
    }
}
exports.LangchainAdapter = LangchainAdapter;
//# sourceMappingURL=langchain.js.map