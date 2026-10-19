"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
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
/** Parse the whole decimal value; final validation runs after overrides merge. */
function environmentNumber(name, fallback) {
    const value = process.env[name];
    if (!value)
        return fallback;
    return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)
        ? Number(value) : NaN;
}
function environmentBoolean(name, fallback) {
    const value = process.env[name];
    if (!value)
        return fallback;
    return value === 'true' ? true : value === 'false' ? false : value;
}
function requireObject(value, name) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError(`${name} must be an object`);
    }
}
function requireInteger(value, name, minimum) {
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) {
        throw new TypeError(`${name} must be a safe integer >= ${minimum}`);
    }
}
/** Get and validate the final configuration, allowing overrides to replace bad env values. */
function getConfig(overrides) {
    const mode = overrides?.storageMode ?? (process.env.CLAUDE_MEMORY_MODE || 'local');
    const config = {
        storageMode: mode,
        memoryPath: process.env.CLAUDE_MEMORY_PATH || (mode === 'shared'
            ? path.join(os.homedir(), '.claude-memory')
            : path.join(process.cwd(), '.claude/memory')),
        autoMine: environmentBoolean('CLAUDE_MEMORY_AUTO_MINE', false),
        defaultSimilarityThreshold: environmentNumber('CLAUDE_MEMORY_THRESHOLD', 0.5),
        defaultMaxResults: environmentNumber('CLAUDE_MEMORY_MAX_RESULTS', 5),
        ...overrides,
    };
    if (config.storageMode !== 'local' && config.storageMode !== 'shared') {
        throw new TypeError('storageMode must be local or shared');
    }
    if (typeof config.memoryPath !== 'string' || !config.memoryPath.trim() || config.memoryPath.includes('\0')) {
        throw new TypeError('memoryPath must be a nonempty path without NUL characters');
    }
    if (typeof config.autoMine !== 'boolean')
        throw new TypeError('autoMine must be a boolean');
    if (typeof config.defaultSimilarityThreshold !== 'number' || !Number.isFinite(config.defaultSimilarityThreshold)
        || config.defaultSimilarityThreshold < 0 || config.defaultSimilarityThreshold > 1) {
        throw new TypeError('defaultSimilarityThreshold must be a finite number in [0, 1]');
    }
    requireInteger(config.defaultMaxResults, 'defaultMaxResults', 0);
    return { ...config, storageMode: config.storageMode, autoMine: config.autoMine };
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
    const totalBudget = environmentNumber('CLAUDE_MEMORY_TOKEN_BUDGET', 2500);
    const config = {
        budget: {
            total: totalBudget,
            allocated: {
                patterns: Math.floor(totalBudget * 0.3),
                incidents: Math.floor(totalBudget * 0.6),
                metadata: Math.floor(totalBudget * 0.1),
            },
            perItem: { pattern: 120, incident: 200, summary: 100 },
        },
        defaultTier: process.env.CLAUDE_MEMORY_TIER || 'compact',
        autoAdjust: environmentBoolean('CLAUDE_MEMORY_AUTO_ADJUST', true),
        ...overrides,
    };
    if (config.defaultTier !== 'summary' && config.defaultTier !== 'compact' && config.defaultTier !== 'full') {
        throw new TypeError('defaultTier must be summary, compact or full');
    }
    if (typeof config.autoAdjust !== 'boolean')
        throw new TypeError('autoAdjust must be a boolean');
    requireObject(config.budget, 'budget');
    requireInteger(config.budget.total, 'budget.total', 1);
    requireObject(config.budget.allocated, 'budget.allocated');
    let remaining = config.budget.total;
    for (const key of ['patterns', 'incidents', 'metadata']) {
        const allocation = config.budget.allocated[key];
        requireInteger(allocation, `budget.allocated.${key}`, 0);
        // Subtract rather than summing, so even safe integers cannot overflow the sum.
        if (allocation > remaining)
            throw new TypeError('budget allocations must not exceed total');
        remaining -= allocation;
    }
    requireObject(config.budget.perItem, 'budget.perItem');
    for (const key of ['pattern', 'incident', 'summary']) {
        requireInteger(config.budget.perItem[key], `budget.perItem.${key}`, 1);
    }
    return { ...config, defaultTier: config.defaultTier, autoAdjust: config.autoAdjust };
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
 * Logs are stored under .build-loop/logs/debugging/ in the project root.
 * Separate from memory paths — these are operational logs, not debugging incidents.
 */
function getLogPaths() {
    return {
        root: path.join(process.cwd(), '.build-loop', 'logs', 'debugging'),
        operations: path.join(process.cwd(), '.build-loop', 'logs', 'debugging', 'operations.jsonl'),
        errors: path.join(process.cwd(), '.build-loop', 'logs', 'debugging', 'errors.jsonl'),
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