"use strict";
/**
 * Configuration System
 *
 * Supports two modes:
 * - local: Each project has its own .claude/memory/ directory
 * - shared: All projects share ~/.claude-memory/ directory
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.getConfig = getConfig;
exports.getMemoryPaths = getMemoryPaths;
exports.displayConfig = displayConfig;
exports.getTokenConfig = getTokenConfig;
exports.displayTokenConfig = displayTokenConfig;
exports.getLogPaths = getLogPaths;
exports.getTracePaths = getTracePaths;
const path = __importStar(require("path"));
const os = __importStar(require("os"));
/**
 * Get configuration from environment variables and defaults
 */
function getConfig(overrides) {
    // Check environment variable for mode
    const mode = process.env.CLAUDE_MEMORY_MODE || 'local';
    // Determine memory path based on mode
    let memoryPath;
    if (mode === 'shared') {
        // Shared mode: Use home directory
        const customPath = process.env.CLAUDE_MEMORY_PATH;
        memoryPath = customPath || path.join(os.homedir(), '.claude-memory');
    }
    else {
        // Local mode: Use current working directory
        const customPath = process.env.CLAUDE_MEMORY_PATH;
        memoryPath = customPath || path.join(process.cwd(), '.claude/memory');
    }
    const config = {
        storageMode: mode,
        memoryPath,
        autoMine: process.env.CLAUDE_MEMORY_AUTO_MINE === 'true',
        defaultSimilarityThreshold: parseFloat(process.env.CLAUDE_MEMORY_THRESHOLD || '0.5'),
        defaultMaxResults: parseInt(process.env.CLAUDE_MEMORY_MAX_RESULTS || '5', 10),
        ...overrides
    };
    return config;
}
/**
 * Get paths for different memory components
 */
function getMemoryPaths(config) {
    const cfg = config || getConfig();
    return {
        incidents: path.join(cfg.memoryPath, 'incidents'),
        patterns: path.join(cfg.memoryPath, 'patterns'),
        sessions: path.join(cfg.memoryPath, 'sessions'),
        root: cfg.memoryPath
    };
}
/**
 * Display current configuration
 */
function displayConfig(config) {
    const cfg = config || getConfig();
    console.log('📊 Memory Configuration:\n');
    console.log(`   Mode: ${cfg.storageMode}`);
    console.log(`   Path: ${cfg.memoryPath}`);
    console.log(`   Auto-mine: ${cfg.autoMine ? 'enabled' : 'disabled'}`);
    console.log(`   Similarity threshold: ${(cfg.defaultSimilarityThreshold * 100).toFixed(0)}%`);
    console.log(`   Max results: ${cfg.defaultMaxResults}\n`);
}
/**
 * Get token configuration from environment variables
 *
 * Environment variables:
 * - CLAUDE_MEMORY_TOKEN_BUDGET: Total token budget (default: 2500)
 * - CLAUDE_MEMORY_TIER: Default retrieval tier (summary|compact|full, default: compact)
 * - CLAUDE_MEMORY_AUTO_ADJUST: Auto-adjust tier if over budget (true|false, default: true)
 */
function getTokenConfig(overrides) {
    const totalBudget = parseInt(process.env.CLAUDE_MEMORY_TOKEN_BUDGET || '2500', 10);
    const tier = process.env.CLAUDE_MEMORY_TIER || 'compact';
    const autoAdjust = process.env.CLAUDE_MEMORY_AUTO_ADJUST !== 'false';
    const config = {
        budget: {
            total: totalBudget,
            allocated: {
                patterns: Math.floor(totalBudget * 0.3),
                incidents: Math.floor(totalBudget * 0.6),
                metadata: Math.floor(totalBudget * 0.1),
            },
            perItem: {
                pattern: 120,
                incident: 200,
                summary: 100,
            },
        },
        defaultTier: tier,
        autoAdjust,
        ...overrides,
    };
    return config;
}
/**
 * Display token configuration
 */
function displayTokenConfig(config) {
    const cfg = config || getTokenConfig();
    console.log('🎯 Token Configuration:\n');
    console.log(`   Total budget: ${cfg.budget.total} tokens`);
    console.log(`   Patterns allocation: ${cfg.budget.allocated.patterns} tokens`);
    console.log(`   Incidents allocation: ${cfg.budget.allocated.incidents} tokens`);
    console.log(`   Default tier: ${cfg.defaultTier}`);
    console.log(`   Auto-adjust: ${cfg.autoAdjust ? 'enabled' : 'disabled'}\n`);
}
/**
 * Get paths for internal logger storage
 *
 * Logs are stored under .claude-code-debugger/logs/ in the project root.
 * Separate from memory paths — these are operational logs, not debugging incidents.
 */
function getLogPaths() {
    return {
        root: path.join(process.cwd(), '.claude-code-debugger', 'logs'),
        operations: path.join(process.cwd(), '.claude-code-debugger', 'logs', 'operations.jsonl'),
        errors: path.join(process.cwd(), '.claude-code-debugger', 'logs', 'errors.jsonl'),
    };
}
/**
 * Get paths for trace storage
 */
function getTracePaths(config) {
    const cfg = config || getConfig();
    return {
        traces: path.join(cfg.memoryPath, 'traces'),
        index: path.join(cfg.memoryPath, 'traces', 'index.json'),
        collections: path.join(cfg.memoryPath, 'traces', 'collections'),
        raw: path.join(cfg.memoryPath, 'traces', 'raw'),
        correlations: path.join(cfg.memoryPath, 'correlations'),
    };
}
//# sourceMappingURL=config.js.map