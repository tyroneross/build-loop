/**
 * Trace Summarizer
 *
 * Token-budget-aware summarization of traces for LLM context injection.
 * Prioritizes errors and performance issues within token limits.
 */

import type {
  UnifiedTrace,
  TraceSummary,
  SummarizationConfig,
  SummarySection,
  TraceSeverity,
} from './types';

// ============================================================================
// SUMMARIZER
// ============================================================================

/**
 * Default summarization configuration
 */
const DEFAULT_CONFIG: SummarizationConfig = {
  max_tokens_per_trace: 100,
  max_tokens_total: 2000,
  prioritize_errors: true,
  include_performance: true,
};

/**
 * TraceSummarizer - Main class for trace summarization
 */
export class TraceSummarizer {
  private config: SummarizationConfig;

  constructor(config: Partial<SummarizationConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Generate context-efficient summary for LLM injection
   *
   * Structure:
   * 1. Errors (highest priority)
   * 2. Performance issues
   * 3. Timeline of operations
   */
  summarizeForContext(traces: UnifiedTrace[]): string {
    // Sort by relevance: errors first, then by severity, then by recency
    const sorted = this.sortByRelevance(traces);

    // Build summary within token budget
    const sections: string[] = [];
    let tokensUsed = 0;

    // Error summary (highest priority)
    if (this.config.prioritize_errors) {
      const errors = sorted.filter(
        (t) => t.severity === 'error' || t.severity === 'fatal'
      );
      if (errors.length > 0) {
        const errorSummary = this.buildErrorSummary(errors);
        sections.push(errorSummary.text);
        tokensUsed += errorSummary.tokens;
      }
    }

    // Performance summary
    if (
      this.config.include_performance &&
      tokensUsed < this.config.max_tokens_total * 0.7
    ) {
      const perfSummary = this.buildPerformanceSummary(sorted);
      if (perfSummary) {
        sections.push(perfSummary.text);
        tokensUsed += perfSummary.tokens;
      }
    }

    // Timeline summary (remaining budget)
    if (tokensUsed < this.config.max_tokens_total * 0.9) {
      const timeline = this.buildTimelineSummary(
        sorted,
        this.config.max_tokens_total - tokensUsed
      );
      sections.push(timeline.text);
    }

    return sections.join('\n\n');
  }

  /**
   * Build error summary section
   */
  private buildErrorSummary(errors: UnifiedTrace[]): SummarySection {
    const lines = ['## Errors Found'];

    for (const error of errors.slice(0, 5)) {
      const summary = error.summary;
      lines.push(
        `- **${error.operation}**: ${summary.error?.message || summary.description}`
      );
      if (summary.error?.stack_preview) {
        lines.push(`  Stack: ${summary.error.stack_preview.split('\n')[0]}`);
      }
    }

    if (errors.length > 5) {
      lines.push(`  ... and ${errors.length - 5} more errors`);
    }

    const text = lines.join('\n');
    return { text, tokens: this.estimateTokens(text) };
  }

  /**
   * Build performance summary section
   */
  private buildPerformanceSummary(
    traces: UnifiedTrace[]
  ): SummarySection | null {
    const slowTraces = traces
      .filter((t) => t.summary.performance?.is_slow)
      .sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));

    if (slowTraces.length === 0) return null;

    const lines = ['## Performance Issues'];

    for (const trace of slowTraces.slice(0, 3)) {
      lines.push(`- **${trace.operation}**: ${trace.duration_ms}ms`);
      if (trace.summary.performance?.bottleneck) {
        lines.push(`  Bottleneck: ${trace.summary.performance.bottleneck}`);
      }
    }

    const text = lines.join('\n');
    return { text, tokens: this.estimateTokens(text) };
  }

  /**
   * Build timeline summary section
   */
  private buildTimelineSummary(
    traces: UnifiedTrace[],
    maxTokens: number
  ): SummarySection {
    const lines = ['## Trace Timeline'];
    let tokens = 20;

    for (const trace of traces) {
      const line = `- [${trace.category}] ${trace.operation}: ${trace.summary.description}`;
      const lineTokens = this.estimateTokens(line);

      if (tokens + lineTokens > maxTokens) break;

      lines.push(line);
      tokens += lineTokens;
    }

    const text = lines.join('\n');
    return { text, tokens };
  }

  /**
   * Sort traces by relevance for summarization
   */
  private sortByRelevance(traces: UnifiedTrace[]): UnifiedTrace[] {
    const severityOrder: Record<TraceSeverity, number> = {
      fatal: 0,
      error: 1,
      warning: 2,
      info: 3,
      debug: 4,
    };

    return [...traces].sort((a, b) => {
      // Errors first
      const severityDiff = severityOrder[a.severity] - severityOrder[b.severity];
      if (severityDiff !== 0) return severityDiff;

      // Then by recency
      return b.timestamp - a.timestamp;
    });
  }

  /**
   * Estimate tokens for text
   */
  estimateTokens(text: string): number {
    // Rough estimate: 4 chars = 1 token
    return Math.ceil(text.length / 4);
  }
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Create a compact trace summary for single trace
 */
export function createTraceSummary(
  operation: string,
  description: string,
  options?: {
    error?: { type: string; message: string; stack_preview?: string };
    performance?: { duration_ms: number; is_slow: boolean; bottleneck?: string };
    highlights?: Array<{ label: string; value: string }>;
  }
): TraceSummary {
  return {
    description: truncate(description, 200),
    highlights: options?.highlights?.slice(0, 5) || [],
    error: options?.error,
    performance: options?.performance,
  };
}

/**
 * Truncate text to max length
 */
function truncate(text: string, maxLen: number): string {
  if (!text || text.length <= maxLen) return text || '';
  return text.substring(0, maxLen - 3) + '...';
}

/**
 * Summarize multiple traces into a single paragraph
 */
export function summarizeTracesCompact(traces: UnifiedTrace[]): string {
  if (traces.length === 0) return 'No traces available.';

  const errorCount = traces.filter(
    (t) => t.severity === 'error' || t.severity === 'fatal'
  ).length;
  const slowCount = traces.filter((t) => t.summary.performance?.is_slow).length;

  const parts: string[] = [];
  parts.push(`${traces.length} traces`);

  if (errorCount > 0) {
    parts.push(`${errorCount} errors`);
  }
  if (slowCount > 0) {
    parts.push(`${slowCount} slow operations`);
  }

  // Get unique categories
  const categories = [...new Set(traces.map((t) => t.category))];
  if (categories.length <= 3) {
    parts.push(`categories: ${categories.join(', ')}`);
  }

  // Time range
  const timestamps = traces.map((t) => t.timestamp);
  const minTime = new Date(Math.min(...timestamps)).toISOString().slice(0, 19);
  const maxTime = new Date(Math.max(...timestamps)).toISOString().slice(0, 19);
  parts.push(`from ${minTime} to ${maxTime}`);

  return parts.join(' | ');
}

/**
 * Filter traces by time window
 */
export function filterByTimeWindow(
  traces: UnifiedTrace[],
  windowMinutes: number
): UnifiedTrace[] {
  const cutoff = Date.now() - windowMinutes * 60 * 1000;
  return traces.filter((t) => t.timestamp >= cutoff);
}

/**
 * Group traces by category
 */
export function groupByCategory(
  traces: UnifiedTrace[]
): Record<string, UnifiedTrace[]> {
  const grouped: Record<string, UnifiedTrace[]> = {};

  for (const trace of traces) {
    if (!grouped[trace.category]) {
      grouped[trace.category] = [];
    }
    grouped[trace.category].push(trace);
  }

  return grouped;
}

/**
 * Get top errors from traces
 */
export function getTopErrors(
  traces: UnifiedTrace[],
  limit: number = 5
): UnifiedTrace[] {
  return traces
    .filter((t) => t.severity === 'error' || t.severity === 'fatal')
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, limit);
}

/**
 * Get slowest operations from traces
 */
export function getSlowestOperations(
  traces: UnifiedTrace[],
  limit: number = 5
): UnifiedTrace[] {
  return traces
    .filter((t) => t.duration_ms !== undefined)
    .sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0))
    .slice(0, limit);
}
