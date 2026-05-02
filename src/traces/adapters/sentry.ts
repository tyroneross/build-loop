/**
 * Sentry Adapter
 *
 * Parses Sentry error events and breadcrumbs into unified format.
 */

import type {
  TraceAdapter,
  TraceSource,
  TraceCategory,
  TraceSeverity,
  UnifiedTrace,
  TraceSummary,
  TraceHighlight,
  SentryEvent,
} from '../types';
import { generateTraceId, storeTrace } from '../storage';
import { createTraceSummary } from '../summarizer';

export class SentryAdapter implements TraceAdapter<SentryEvent> {
  readonly source: TraceSource = 'sentry';

  /**
   * Validate if input is Sentry event format
   */
  validate(input: unknown): input is SentryEvent {
    if (!input || typeof input !== 'object') return false;
    const obj = input as Record<string, unknown>;
    return (
      typeof obj.event_id === 'string' &&
      typeof obj.timestamp === 'string' &&
      (obj.exception !== undefined || obj.level !== undefined)
    );
  }

  /**
   * Parse Sentry event into unified traces
   */
  async parse(input: SentryEvent): Promise<UnifiedTrace[]> {
    const traces: UnifiedTrace[] = [];

    // Main error event
    traces.push(this.convertEvent(input));

    // Breadcrumbs as supporting traces
    if (input.breadcrumbs?.values) {
      for (const crumb of input.breadcrumbs.values) {
        traces.push(this.convertBreadcrumb(crumb, input.event_id));
      }
    }

    return traces;
  }

  /**
   * Convert Sentry event to unified trace
   */
  private convertEvent(event: SentryEvent): UnifiedTrace {
    const exception = event.exception?.values?.[0];
    const timestamp = new Date(event.timestamp).getTime();

    return {
      trace_id: generateTraceId(),
      source: 'sentry',
      external_id: event.event_id,
      timestamp,
      severity: this.mapSeverity(event.level),
      category: 'console-error',
      operation: event.transaction || exception?.type || 'Error',
      summary: this.buildEventSummary(event),
      has_full_data: true,
      tokens_estimated: 80,
    };
  }

  /**
   * Convert breadcrumb to unified trace
   */
  private convertBreadcrumb(
    crumb: NonNullable<SentryEvent['breadcrumbs']>['values'][0],
    parentEventId: string
  ): UnifiedTrace {
    const timestamp = new Date(crumb.timestamp).getTime();

    return {
      trace_id: generateTraceId(),
      source: 'sentry',
      external_id: `${parentEventId}_breadcrumb`,
      timestamp,
      severity: this.mapBreadcrumbSeverity(crumb.type),
      category: this.mapBreadcrumbCategory(crumb.category),
      operation: crumb.category,
      summary: {
        description: crumb.message || `[${crumb.category}] ${crumb.type}`,
        highlights: this.buildBreadcrumbHighlights(crumb),
      },
      has_full_data: false,
      tokens_estimated: 30,
    };
  }

  /**
   * Create summary from trace
   */
  summarize(trace: UnifiedTrace, rawData: SentryEvent): TraceSummary {
    return trace.summary;
  }

  /**
   * Build event summary
   */
  private buildEventSummary(event: SentryEvent): TraceSummary {
    const exception = event.exception?.values?.[0];
    const highlights: TraceHighlight[] = [];

    // Add context
    if (event.release) {
      highlights.push({ label: 'release', value: event.release });
    }
    if (event.environment) {
      highlights.push({ label: 'env', value: event.environment });
    }
    if (event.transaction) {
      highlights.push({ label: 'transaction', value: event.transaction });
    }

    // Add tags
    if (event.tags) {
      const tagEntries = Object.entries(event.tags).slice(0, 2);
      for (const [key, value] of tagEntries) {
        highlights.push({ label: key, value });
      }
    }

    // Build description
    let description = 'Error';
    if (exception) {
      description = `${exception.type}: ${exception.value.substring(0, 150)}`;
    } else if (event.transaction) {
      description = `Transaction: ${event.transaction}`;
    }

    return createTraceSummary(
      exception?.type || 'Error',
      description,
      {
        highlights: highlights.slice(0, 5),
        error: exception
          ? {
              type: exception.type,
              message: exception.value.substring(0, 200),
              stack_preview: this.extractStackPreview(exception.stacktrace),
            }
          : undefined,
      }
    );
  }

  /**
   * Build breadcrumb highlights
   */
  private buildBreadcrumbHighlights(
    crumb: NonNullable<SentryEvent['breadcrumbs']>['values'][0]
  ): TraceHighlight[] {
    const highlights: TraceHighlight[] = [];

    highlights.push({ label: 'type', value: crumb.type });
    highlights.push({ label: 'category', value: crumb.category });

    if (crumb.data) {
      const dataEntries = Object.entries(crumb.data).slice(0, 2);
      for (const [key, value] of dataEntries) {
        highlights.push({
          label: key,
          value: String(value).substring(0, 50),
        });
      }
    }

    return highlights;
  }

  /**
   * Extract stack preview from stacktrace
   */
  private extractStackPreview(
    stacktrace?: { frames: Array<{ filename: string; function: string; lineno?: number }> }
  ): string | undefined {
    if (!stacktrace?.frames) return undefined;

    // Get last 3 frames (most relevant)
    const relevantFrames = stacktrace.frames.slice(-3).reverse();
    return relevantFrames
      .map((f: { filename: string; function: string; lineno?: number }) =>
        `${f.function || 'anonymous'} (${f.filename}:${f.lineno})`
      )
      .join('\n');
  }

  /**
   * Store full data for lazy loading
   */
  async storeFullData(trace: UnifiedTrace, rawData: SentryEvent): Promise<string> {
    const result = await storeTrace(trace, rawData);
    return result.file_path;
  }

  /**
   * Map Sentry level to trace severity
   */
  private mapSeverity(level: SentryEvent['level']): TraceSeverity {
    switch (level) {
      case 'fatal':
        return 'fatal';
      case 'error':
        return 'error';
      case 'warning':
        return 'warning';
      case 'info':
        return 'info';
      case 'debug':
        return 'debug';
      default:
        return 'info';
    }
  }

  /**
   * Map breadcrumb type to severity
   */
  private mapBreadcrumbSeverity(type: string): TraceSeverity {
    if (type === 'error') return 'error';
    if (type === 'warning') return 'warning';
    return 'info';
  }

  /**
   * Map breadcrumb category to trace category
   */
  private mapBreadcrumbCategory(category: string): TraceCategory {
    if (category === 'http' || category === 'fetch' || category === 'xhr') {
      return 'http-request';
    }
    if (category === 'console') return 'console-log';
    if (category === 'navigation' || category === 'ui') return 'ui-interaction';
    return 'custom';
  }
}
