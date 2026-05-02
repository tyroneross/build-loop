/**
 * OpenTelemetry Adapter
 *
 * Parses OTLP JSON format traces into unified format.
 */

import type {
  TraceAdapter,
  TraceSource,
  TraceCategory,
  TraceSeverity,
  UnifiedTrace,
  TraceSummary,
  TraceHighlight,
  OTLPTrace,
  OTLPSpan,
  OTLPValue,
} from '../types';
import { generateTraceId, storeTrace } from '../storage';
import { createTraceSummary } from '../summarizer';

export class OpenTelemetryAdapter implements TraceAdapter<OTLPTrace> {
  readonly source: TraceSource = 'opentelemetry';

  /**
   * Validate if input is OTLP format
   */
  validate(input: unknown): input is OTLPTrace {
    if (!input || typeof input !== 'object') return false;
    const obj = input as Record<string, unknown>;
    return Array.isArray(obj.resourceSpans);
  }

  /**
   * Parse OTLP trace into unified traces
   */
  async parse(input: OTLPTrace): Promise<UnifiedTrace[]> {
    const traces: UnifiedTrace[] = [];

    for (const resourceSpan of input.resourceSpans) {
      const serviceName = this.extractAttribute(
        resourceSpan.resource?.attributes || [],
        'service.name'
      );

      for (const scopeSpan of resourceSpan.scopeSpans || []) {
        for (const span of scopeSpan.spans || []) {
          traces.push(this.convertSpan(span, serviceName));
        }
      }
    }

    return traces;
  }

  /**
   * Convert single OTLP span to unified trace
   */
  private convertSpan(span: OTLPSpan, serviceName?: string): UnifiedTrace {
    const startNano = BigInt(span.startTimeUnixNano);
    const endNano = BigInt(span.endTimeUnixNano);
    const durationMs = Number((endNano - startNano) / BigInt(1_000_000));

    const hasError = span.status?.code === 2;
    const severity: TraceSeverity = hasError ? 'error' : 'info';

    return {
      trace_id: generateTraceId(),
      source: 'opentelemetry',
      external_id: span.traceId,
      timestamp: Number(startNano / BigInt(1_000_000)),
      duration_ms: durationMs,
      severity,
      category: this.inferCategory(span),
      operation: span.name,
      summary: this.summarizeSpan(span, serviceName, durationMs, hasError),
      has_full_data: true,
      tokens_estimated: 50,
    };
  }

  /**
   * Create summary for span
   */
  summarize(trace: UnifiedTrace, rawData: OTLPTrace): TraceSummary {
    return trace.summary;
  }

  /**
   * Summarize a single span
   */
  private summarizeSpan(
    span: OTLPSpan,
    serviceName: string | undefined,
    durationMs: number,
    hasError: boolean
  ): TraceSummary {
    const highlights: TraceHighlight[] = [];

    // Add service name if available
    if (serviceName) {
      highlights.push({ label: 'service', value: serviceName });
    }

    // Add HTTP info if available
    const httpMethod = this.extractAttribute(span.attributes || [], 'http.method');
    const httpUrl = this.extractAttribute(span.attributes || [], 'http.url');
    if (httpMethod) highlights.push({ label: 'method', value: httpMethod });
    if (httpUrl) highlights.push({ label: 'url', value: this.truncateUrl(httpUrl) });

    // Add database info if available
    const dbSystem = this.extractAttribute(span.attributes || [], 'db.system');
    const dbStatement = this.extractAttribute(span.attributes || [], 'db.statement');
    if (dbSystem) highlights.push({ label: 'database', value: dbSystem });
    if (dbStatement) highlights.push({ label: 'query', value: dbStatement.substring(0, 50) });

    // Build description
    let description = span.name;
    if (httpMethod && httpUrl) {
      description = `${httpMethod} ${this.truncateUrl(httpUrl)}`;
    } else if (dbStatement) {
      description = `DB: ${dbStatement.substring(0, 80)}`;
    }

    return createTraceSummary(span.name, description, {
      highlights: highlights.slice(0, 5),
      error: hasError && span.status?.message
        ? { type: 'SpanError', message: span.status.message }
        : undefined,
      performance: {
        duration_ms: durationMs,
        is_slow: durationMs > 1000, // > 1 second is slow
        bottleneck: durationMs > 5000 ? 'Very slow operation' : undefined,
      },
    });
  }

  /**
   * Store full data for lazy loading
   */
  async storeFullData(trace: UnifiedTrace, rawData: OTLPTrace): Promise<string> {
    const result = await storeTrace(trace, rawData);
    return result.file_path;
  }

  /**
   * Infer category from span attributes
   */
  private inferCategory(span: OTLPSpan): TraceCategory {
    const name = span.name.toLowerCase();
    const attributes = span.attributes || [];

    const httpMethod = this.extractAttribute(attributes, 'http.method');
    if (httpMethod) return 'http-request';

    const dbSystem = this.extractAttribute(attributes, 'db.system');
    if (dbSystem) return 'database-query';

    if (name.includes('cache') || name.includes('redis')) return 'cache-operation';
    if (name.includes('llm') || name.includes('openai')) return 'llm-call';

    return 'custom';
  }

  /**
   * Extract attribute value from OTLP attributes
   */
  private extractAttribute(
    attributes: Array<{ key: string; value: OTLPValue }>,
    key: string
  ): string | undefined {
    const attr = attributes.find((a) => a.key === key);
    if (!attr) return undefined;

    const value = attr.value;
    return value.stringValue || value.intValue?.toString() || undefined;
  }

  /**
   * Truncate URL for display
   */
  private truncateUrl(url: string): string {
    if (url.length <= 60) return url;
    try {
      const parsed = new URL(url);
      return `${parsed.pathname}${parsed.search ? '?...' : ''}`;
    } catch {
      return url.substring(0, 60) + '...';
    }
  }
}
