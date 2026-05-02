/**
 * Langchain Adapter
 *
 * Parses LangSmith run format into unified traces.
 */

import type {
  TraceAdapter,
  TraceSource,
  TraceCategory,
  TraceSeverity,
  UnifiedTrace,
  TraceSummary,
  TraceHighlight,
  LangSmithRun,
} from '../types';
import { generateTraceId, storeTrace } from '../storage';
import { createTraceSummary } from '../summarizer';

export class LangchainAdapter implements TraceAdapter<LangSmithRun[]> {
  readonly source: TraceSource = 'langchain';

  /**
   * Validate if input is LangSmith run format
   */
  validate(input: unknown): input is LangSmithRun[] {
    if (!Array.isArray(input)) return false;
    if (input.length === 0) return true; // Empty array is valid

    const first = input[0];
    return (
      typeof first === 'object' &&
      first !== null &&
      typeof first.id === 'string' &&
      typeof first.name === 'string' &&
      typeof first.run_type === 'string'
    );
  }

  /**
   * Parse LangSmith runs into unified traces
   */
  async parse(input: LangSmithRun[]): Promise<UnifiedTrace[]> {
    return input.map((run) => this.convertRun(run));
  }

  /**
   * Convert single LangSmith run to unified trace
   */
  private convertRun(run: LangSmithRun): UnifiedTrace {
    const startTime = new Date(run.start_time).getTime();
    const endTime = run.end_time ? new Date(run.end_time).getTime() : undefined;
    const durationMs = endTime ? endTime - startTime : undefined;

    return {
      trace_id: generateTraceId(),
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
  summarize(trace: UnifiedTrace, rawData: LangSmithRun[]): TraceSummary {
    return trace.summary;
  }

  /**
   * Build run summary
   */
  private buildRunSummary(run: LangSmithRun, durationMs?: number): TraceSummary {
    const highlights: TraceHighlight[] = [];

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

    return createTraceSummary(run.name, description, {
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
  async storeFullData(
    trace: UnifiedTrace,
    rawData: LangSmithRun[]
  ): Promise<string> {
    const result = await storeTrace(trace, rawData);
    return result.file_path;
  }

  /**
   * Map run type to trace category
   */
  private mapRunType(runType: LangSmithRun['run_type']): TraceCategory {
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
  private identifyBottleneck(
    run: LangSmithRun,
    durationMs: number
  ): string | undefined {
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
