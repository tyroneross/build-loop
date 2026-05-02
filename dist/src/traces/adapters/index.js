"use strict";
/**
 * Trace Adapters - Entry Point
 *
 * Exports all trace adapters for different sources.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.BrowserTraceAdapter = exports.LangchainAdapter = exports.SentryAdapter = exports.OpenTelemetryAdapter = void 0;
exports.getAdapter = getAdapter;
exports.autoParseTraces = autoParseTraces;
var opentelemetry_1 = require("./opentelemetry");
Object.defineProperty(exports, "OpenTelemetryAdapter", { enumerable: true, get: function () { return opentelemetry_1.OpenTelemetryAdapter; } });
var sentry_1 = require("./sentry");
Object.defineProperty(exports, "SentryAdapter", { enumerable: true, get: function () { return sentry_1.SentryAdapter; } });
var langchain_1 = require("./langchain");
Object.defineProperty(exports, "LangchainAdapter", { enumerable: true, get: function () { return langchain_1.LangchainAdapter; } });
var browser_1 = require("./browser");
Object.defineProperty(exports, "BrowserTraceAdapter", { enumerable: true, get: function () { return browser_1.BrowserTraceAdapter; } });
const opentelemetry_2 = require("./opentelemetry");
const sentry_2 = require("./sentry");
const langchain_2 = require("./langchain");
const browser_2 = require("./browser");
/**
 * Get adapter for a specific source
 */
function getAdapter(source) {
    switch (source) {
        case 'opentelemetry':
            return new opentelemetry_2.OpenTelemetryAdapter();
        case 'sentry':
            return new sentry_2.SentryAdapter();
        case 'langchain':
            return new langchain_2.LangchainAdapter();
        case 'chrome-devtools':
        case 'playwright':
        case 'browser-console':
            return new browser_2.BrowserTraceAdapter();
        default:
            throw new Error(`Unknown trace source: ${source}`);
    }
}
/**
 * Auto-detect source and parse traces
 */
async function autoParseTraces(input) {
    // Try each adapter's validate function
    const otelAdapter = new opentelemetry_2.OpenTelemetryAdapter();
    if (otelAdapter.validate(input)) {
        return otelAdapter.parse(input);
    }
    const sentryAdapter = new sentry_2.SentryAdapter();
    if (sentryAdapter.validate(input)) {
        return sentryAdapter.parse(input);
    }
    const langchainAdapter = new langchain_2.LangchainAdapter();
    if (langchainAdapter.validate(input)) {
        return langchainAdapter.parse(input);
    }
    const browserAdapter = new browser_2.BrowserTraceAdapter();
    if (browserAdapter.validate(input)) {
        return browserAdapter.parse(input);
    }
    throw new Error('Unable to detect trace format. Please specify the source explicitly.');
}
//# sourceMappingURL=index.js.map