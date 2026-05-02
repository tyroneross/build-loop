/**
 * Trace Adapters - Entry Point
 *
 * Exports all trace adapters for different sources.
 */

export { OpenTelemetryAdapter } from './opentelemetry';
export { SentryAdapter } from './sentry';
export { LangchainAdapter } from './langchain';
export { BrowserTraceAdapter } from './browser';

import type { TraceSource, UnifiedTrace } from '../types';
import { OpenTelemetryAdapter } from './opentelemetry';
import { SentryAdapter } from './sentry';
import { LangchainAdapter } from './langchain';
import { BrowserTraceAdapter } from './browser';

/**
 * Get adapter for a specific source
 */
export function getAdapter(source: TraceSource) {
  switch (source) {
    case 'opentelemetry':
      return new OpenTelemetryAdapter();
    case 'sentry':
      return new SentryAdapter();
    case 'langchain':
      return new LangchainAdapter();
    case 'chrome-devtools':
    case 'playwright':
    case 'browser-console':
      return new BrowserTraceAdapter();
    default:
      throw new Error(`Unknown trace source: ${source}`);
  }
}

/**
 * Auto-detect source and parse traces
 */
export async function autoParseTraces(input: unknown): Promise<UnifiedTrace[]> {
  // Try each adapter's validate function
  const otelAdapter = new OpenTelemetryAdapter();
  if (otelAdapter.validate(input)) {
    return otelAdapter.parse(input);
  }

  const sentryAdapter = new SentryAdapter();
  if (sentryAdapter.validate(input)) {
    return sentryAdapter.parse(input);
  }

  const langchainAdapter = new LangchainAdapter();
  if (langchainAdapter.validate(input)) {
    return langchainAdapter.parse(input);
  }

  const browserAdapter = new BrowserTraceAdapter();
  if (browserAdapter.validate(input)) {
    return browserAdapter.parse(input);
  }

  throw new Error('Unable to detect trace format. Please specify the source explicitly.');
}
