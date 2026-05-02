"use strict";
/**
 * OpenTelemetry Adapter
 *
 * Parses OTLP JSON format traces into unified format.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.OpenTelemetryAdapter = void 0;
const storage_1 = require("../storage");
const summarizer_1 = require("../summarizer");
class OpenTelemetryAdapter {
    constructor() {
        this.source = 'opentelemetry';
    }
    /**
     * Validate if input is OTLP format
     */
    validate(input) {
        if (!input || typeof input !== 'object')
            return false;
        const obj = input;
        return Array.isArray(obj.resourceSpans);
    }
    /**
     * Parse OTLP trace into unified traces
     */
    async parse(input) {
        const traces = [];
        for (const resourceSpan of input.resourceSpans) {
            const serviceName = this.extractAttribute(resourceSpan.resource?.attributes || [], 'service.name');
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
    convertSpan(span, serviceName) {
        const startNano = BigInt(span.startTimeUnixNano);
        const endNano = BigInt(span.endTimeUnixNano);
        const durationMs = Number((endNano - startNano) / BigInt(1000000));
        const hasError = span.status?.code === 2;
        const severity = hasError ? 'error' : 'info';
        return {
            trace_id: (0, storage_1.generateTraceId)(),
            source: 'opentelemetry',
            external_id: span.traceId,
            timestamp: Number(startNano / BigInt(1000000)),
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
    summarize(trace, rawData) {
        return trace.summary;
    }
    /**
     * Summarize a single span
     */
    summarizeSpan(span, serviceName, durationMs, hasError) {
        const highlights = [];
        // Add service name if available
        if (serviceName) {
            highlights.push({ label: 'service', value: serviceName });
        }
        // Add HTTP info if available
        const httpMethod = this.extractAttribute(span.attributes || [], 'http.method');
        const httpUrl = this.extractAttribute(span.attributes || [], 'http.url');
        if (httpMethod)
            highlights.push({ label: 'method', value: httpMethod });
        if (httpUrl)
            highlights.push({ label: 'url', value: this.truncateUrl(httpUrl) });
        // Add database info if available
        const dbSystem = this.extractAttribute(span.attributes || [], 'db.system');
        const dbStatement = this.extractAttribute(span.attributes || [], 'db.statement');
        if (dbSystem)
            highlights.push({ label: 'database', value: dbSystem });
        if (dbStatement)
            highlights.push({ label: 'query', value: dbStatement.substring(0, 50) });
        // Build description
        let description = span.name;
        if (httpMethod && httpUrl) {
            description = `${httpMethod} ${this.truncateUrl(httpUrl)}`;
        }
        else if (dbStatement) {
            description = `DB: ${dbStatement.substring(0, 80)}`;
        }
        return (0, summarizer_1.createTraceSummary)(span.name, description, {
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
    async storeFullData(trace, rawData) {
        const result = await (0, storage_1.storeTrace)(trace, rawData);
        return result.file_path;
    }
    /**
     * Infer category from span attributes
     */
    inferCategory(span) {
        const name = span.name.toLowerCase();
        const attributes = span.attributes || [];
        const httpMethod = this.extractAttribute(attributes, 'http.method');
        if (httpMethod)
            return 'http-request';
        const dbSystem = this.extractAttribute(attributes, 'db.system');
        if (dbSystem)
            return 'database-query';
        if (name.includes('cache') || name.includes('redis'))
            return 'cache-operation';
        if (name.includes('llm') || name.includes('openai'))
            return 'llm-call';
        return 'custom';
    }
    /**
     * Extract attribute value from OTLP attributes
     */
    extractAttribute(attributes, key) {
        const attr = attributes.find((a) => a.key === key);
        if (!attr)
            return undefined;
        const value = attr.value;
        return value.stringValue || value.intValue?.toString() || undefined;
    }
    /**
     * Truncate URL for display
     */
    truncateUrl(url) {
        if (url.length <= 60)
            return url;
        try {
            const parsed = new URL(url);
            return `${parsed.pathname}${parsed.search ? '?...' : ''}`;
        }
        catch {
            return url.substring(0, 60) + '...';
        }
    }
}
exports.OpenTelemetryAdapter = OpenTelemetryAdapter;
//# sourceMappingURL=opentelemetry.js.map