"use strict";
/**
 * Sentry Adapter
 *
 * Parses Sentry error events and breadcrumbs into unified format.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SentryAdapter = void 0;
const storage_1 = require("../storage");
const summarizer_1 = require("../summarizer");
class SentryAdapter {
    constructor() {
        this.source = 'sentry';
    }
    /**
     * Validate if input is Sentry event format
     */
    validate(input) {
        if (!input || typeof input !== 'object')
            return false;
        const obj = input;
        return (typeof obj.event_id === 'string' &&
            typeof obj.timestamp === 'string' &&
            (obj.exception !== undefined || obj.level !== undefined));
    }
    /**
     * Parse Sentry event into unified traces
     */
    async parse(input) {
        const traces = [];
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
    convertEvent(event) {
        const exception = event.exception?.values?.[0];
        const timestamp = new Date(event.timestamp).getTime();
        return {
            trace_id: (0, storage_1.generateTraceId)(),
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
    convertBreadcrumb(crumb, parentEventId) {
        const timestamp = new Date(crumb.timestamp).getTime();
        return {
            trace_id: (0, storage_1.generateTraceId)(),
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
    summarize(trace, rawData) {
        return trace.summary;
    }
    /**
     * Build event summary
     */
    buildEventSummary(event) {
        const exception = event.exception?.values?.[0];
        const highlights = [];
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
        }
        else if (event.transaction) {
            description = `Transaction: ${event.transaction}`;
        }
        return (0, summarizer_1.createTraceSummary)(exception?.type || 'Error', description, {
            highlights: highlights.slice(0, 5),
            error: exception
                ? {
                    type: exception.type,
                    message: exception.value.substring(0, 200),
                    stack_preview: this.extractStackPreview(exception.stacktrace),
                }
                : undefined,
        });
    }
    /**
     * Build breadcrumb highlights
     */
    buildBreadcrumbHighlights(crumb) {
        const highlights = [];
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
    extractStackPreview(stacktrace) {
        if (!stacktrace?.frames)
            return undefined;
        // Get last 3 frames (most relevant)
        const relevantFrames = stacktrace.frames.slice(-3).reverse();
        return relevantFrames
            .map((f) => `${f.function || 'anonymous'} (${f.filename}:${f.lineno})`)
            .join('\n');
    }
    /**
     * Store full data for lazy loading
     */
    async storeFullData(trace, rawData) {
        const result = await (0, storage_1.storeTrace)(trace, rawData);
        return result.file_path;
    }
    /**
     * Map Sentry level to trace severity
     */
    mapSeverity(level) {
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
    mapBreadcrumbSeverity(type) {
        if (type === 'error')
            return 'error';
        if (type === 'warning')
            return 'warning';
        return 'info';
    }
    /**
     * Map breadcrumb category to trace category
     */
    mapBreadcrumbCategory(category) {
        if (category === 'http' || category === 'fetch' || category === 'xhr') {
            return 'http-request';
        }
        if (category === 'console')
            return 'console-log';
        if (category === 'navigation' || category === 'ui')
            return 'ui-interaction';
        return 'custom';
    }
}
exports.SentryAdapter = SentryAdapter;
//# sourceMappingURL=sentry.js.map