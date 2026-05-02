/**
 * Browser Trace Adapter
 *
 * Parses Chrome DevTools Performance traces, Playwright traces,
 * and browser console logs into unified format.
 */

import type {
  TraceAdapter,
  TraceSource,
  TraceCategory,
  TraceSeverity,
  UnifiedTrace,
  TraceSummary,
  TraceHighlight,
  ChromeTrace,
  ChromeTraceEvent,
  PlaywrightTrace,
  PlaywrightAction,
  HAREntry,
  ConsoleMessage,
} from '../types';
import { generateTraceId, storeTrace } from '../storage';
import { createTraceSummary } from '../summarizer';

type BrowserInput = ChromeTrace | PlaywrightTrace;

export class BrowserTraceAdapter implements TraceAdapter<BrowserInput> {
  readonly source: TraceSource = 'chrome-devtools';

  /**
   * Validate if input is a browser trace format
   */
  validate(input: unknown): input is BrowserInput {
    if (!input || typeof input !== 'object') return false;
    const obj = input as Record<string, unknown>;

    // Chrome DevTools format
    if (Array.isArray(obj.traceEvents)) {
      return true;
    }

    // Playwright format
    if (Array.isArray(obj.actions) || Array.isArray(obj.network)) {
      return true;
    }

    return false;
  }

  /**
   * Parse browser traces into unified format
   */
  async parse(input: BrowserInput): Promise<UnifiedTrace[]> {
    if ('traceEvents' in input) {
      return this.parseChrome(input);
    } else {
      return this.parsePlaywright(input);
    }
  }

  /**
   * Create summary from trace
   */
  summarize(trace: UnifiedTrace, rawData: BrowserInput): TraceSummary {
    return trace.summary;
  }

  /**
   * Store full data for lazy loading
   */
  async storeFullData(
    trace: UnifiedTrace,
    rawData: BrowserInput
  ): Promise<string> {
    const result = await storeTrace(trace, rawData);
    return result.file_path;
  }

  // =========================================================================
  // CHROME DEVTOOLS PARSING
  // =========================================================================

  /**
   * Parse Chrome DevTools performance trace
   */
  private parseChrome(trace: ChromeTrace): UnifiedTrace[] {
    const traces: UnifiedTrace[] = [];

    // Filter for significant events (avoid noise)
    const significantEvents = trace.traceEvents.filter(
      (e) =>
        e.ph === 'X' && // Complete events
        e.dur && // Has duration
        e.dur > 1000 && // > 1ms
        this.isRelevantCategory(e.cat)
    );

    // Group by category and summarize
    const grouped = this.groupByCategory(significantEvents);

    for (const [category, events] of Object.entries(grouped)) {
      traces.push(this.summarizeChromeGroup(category, events));
    }

    return traces;
  }

  /**
   * Summarize a group of Chrome events
   */
  private summarizeChromeGroup(
    category: string,
    events: ChromeTraceEvent[]
  ): UnifiedTrace {
    const totalDuration = events.reduce((sum, e) => sum + (e.dur || 0), 0);
    const avgDuration = totalDuration / events.length;
    const slowest = events.reduce(
      (max, e) => ((e.dur || 0) > (max.dur || 0) ? e : max),
      events[0]
    );

    // Convert microseconds to milliseconds
    const durationMs = totalDuration / 1000;

    return {
      trace_id: generateTraceId(),
      source: 'chrome-devtools',
      timestamp: events[0].ts / 1000,
      duration_ms: durationMs,
      severity: durationMs > 1000 ? 'warning' : 'info',
      category: this.mapChromeCategory(category),
      operation: `${category} (${events.length} events)`,
      summary: createTraceSummary(
        category,
        `${events.length} ${category} events, total ${durationMs.toFixed(0)}ms`,
        {
          highlights: [
            { label: 'events', value: String(events.length) },
            { label: 'total', value: `${durationMs.toFixed(0)}ms` },
            { label: 'avg', value: `${(avgDuration / 1000).toFixed(1)}ms` },
            { label: 'slowest', value: slowest.name },
          ],
          performance: {
            duration_ms: durationMs,
            is_slow: durationMs > 500,
            bottleneck: durationMs > 1000 ? `${category} overhead` : undefined,
          },
        }
      ),
      has_full_data: true,
      tokens_estimated: 40,
    };
  }

  /**
   * Check if Chrome category is relevant
   */
  private isRelevantCategory(cat?: string): boolean {
    if (!cat) return false;
    const relevant = [
      'devtools.timeline',
      'blink.user_timing',
      'v8',
      'loading',
      'paint',
      'layout',
    ];
    return relevant.some((r) => cat.includes(r));
  }

  /**
   * Group Chrome events by category
   */
  private groupByCategory(
    events: ChromeTraceEvent[]
  ): Record<string, ChromeTraceEvent[]> {
    const grouped: Record<string, ChromeTraceEvent[]> = {};

    for (const event of events) {
      const cat = event.cat || 'other';
      const mainCat = cat.split(',')[0];
      if (!grouped[mainCat]) {
        grouped[mainCat] = [];
      }
      grouped[mainCat].push(event);
    }

    return grouped;
  }

  /**
   * Map Chrome category to trace category
   */
  private mapChromeCategory(cat: string): TraceCategory {
    if (cat.includes('paint') || cat.includes('layout')) return 'ui-render';
    if (cat.includes('v8') || cat.includes('script')) return 'custom';
    if (cat.includes('loading')) return 'network-request';
    return 'performance-metric';
  }

  // =========================================================================
  // PLAYWRIGHT PARSING
  // =========================================================================

  /**
   * Parse Playwright trace
   */
  private parsePlaywright(trace: PlaywrightTrace): UnifiedTrace[] {
    const traces: UnifiedTrace[] = [];

    // Actions
    if (trace.actions) {
      for (const action of trace.actions) {
        traces.push(this.convertPlaywrightAction(action));
      }
    }

    // Failed network requests
    if (trace.network) {
      for (const entry of trace.network) {
        if (entry.response.status >= 400) {
          traces.push(this.convertNetworkError(entry));
        }
      }
    }

    // Console errors
    if (trace.console) {
      for (const log of trace.console.filter((c) => c.type === 'error')) {
        traces.push(this.convertConsoleError(log));
      }
    }

    return traces;
  }

  /**
   * Convert Playwright action to trace
   */
  private convertPlaywrightAction(action: PlaywrightAction): UnifiedTrace {
    const durationMs = action.endTime - action.startTime;

    return {
      trace_id: generateTraceId(),
      source: 'playwright',
      timestamp: action.startTime,
      duration_ms: durationMs,
      severity: action.error ? 'error' : 'info',
      category: 'ui-interaction',
      operation: action.name,
      summary: createTraceSummary(action.name, `Action: ${action.name}`, {
        highlights: [{ label: 'duration', value: `${durationMs}ms` }],
        error: action.error
          ? {
              type: 'ActionError',
              message: action.error.substring(0, 200),
            }
          : undefined,
        performance: {
          duration_ms: durationMs,
          is_slow: durationMs > 5000,
        },
      }),
      has_full_data: action.snapshots ? action.snapshots.length > 0 : false,
      tokens_estimated: 35,
    };
  }

  /**
   * Convert network error to trace
   */
  private convertNetworkError(entry: HAREntry): UnifiedTrace {
    const timestamp = new Date(entry.startedDateTime).getTime();

    return {
      trace_id: generateTraceId(),
      source: 'playwright',
      timestamp,
      duration_ms: entry.time,
      severity: entry.response.status >= 500 ? 'error' : 'warning',
      category: 'http-response',
      operation: `${entry.request.method} ${this.truncateUrl(entry.request.url)}`,
      summary: createTraceSummary(
        'Network Error',
        `${entry.response.status} ${entry.response.statusText}: ${entry.request.method} ${this.truncateUrl(entry.request.url)}`,
        {
          highlights: [
            { label: 'status', value: String(entry.response.status) },
            { label: 'method', value: entry.request.method },
          ],
          error: {
            type: 'HTTPError',
            message: `${entry.response.status} ${entry.response.statusText}`,
          },
        }
      ),
      has_full_data: true,
      tokens_estimated: 40,
    };
  }

  /**
   * Convert console error to trace
   */
  private convertConsoleError(log: ConsoleMessage): UnifiedTrace {
    return {
      trace_id: generateTraceId(),
      source: 'browser-console',
      timestamp: log.timestamp,
      severity: 'error',
      category: 'console-error',
      operation: 'Console Error',
      summary: createTraceSummary('Console Error', log.text.substring(0, 200), {
        error: {
          type: 'ConsoleError',
          message: log.text.substring(0, 200),
        },
      }),
      has_full_data: false,
      tokens_estimated: 30,
    };
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
