// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Configuration System
 *
 * Supports two modes:
 * - local: Each project has its own .claude/memory/ directory
 * - shared: All projects share ~/.claude-memory/ directory
 */

import * as path from 'path';
import * as os from 'os';

import type { MemoryConfig } from './types';
export type { MemoryConfig } from './types';

/** Parse the whole decimal value; final validation runs after overrides merge. */
function environmentNumber(name: string, fallback: number): number {
  const value = process.env[name];
  if (!value) return fallback;
  return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)
    ? Number(value) : NaN;
}

function environmentBoolean(name: string, fallback: boolean): unknown {
  const value = process.env[name];
  if (!value) return fallback;
  return value === 'true' ? true : value === 'false' ? false : value;
}

function requireObject(value: unknown, name: string): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${name} must be an object`);
  }
}

function requireInteger(value: unknown, name: string, minimum: number): asserts value is number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) {
    throw new TypeError(`${name} must be a safe integer >= ${minimum}`);
  }
}

/** Get and validate the final configuration, allowing overrides to replace bad env values. */
export function getConfig(overrides?: Partial<MemoryConfig>): MemoryConfig {
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
  if (typeof config.autoMine !== 'boolean') throw new TypeError('autoMine must be a boolean');
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
export function getMemoryPaths(config?: MemoryConfig) {
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
export function displayConfig(config?: MemoryConfig): void {
  const cfg = config || getConfig();

  console.log('📊 Memory Configuration:\n');
  console.log(`   Mode: ${cfg.storageMode}`);
  console.log(`   Path: ${cfg.memoryPath}`);
  console.log(`   Auto-mine: ${cfg.autoMine ? 'enabled' : 'disabled'}`);
  console.log(`   Similarity threshold: ${(cfg.defaultSimilarityThreshold * 100).toFixed(0)}%`);
  console.log(`   Max results: ${cfg.defaultMaxResults}\n`);
}

// ============================================================================
// TOKEN BUDGET CONFIGURATION
// ============================================================================

import type { TokenBudget } from './types';

/**
 * Token configuration for context injection
 */
export interface TokenConfig {
  /** Total token budget for memory context */
  budget: TokenBudget;
  /** Default retrieval tier */
  defaultTier: 'summary' | 'compact' | 'full';
  /** Auto-adjust tier if budget exceeded */
  autoAdjust: boolean;
}

/**
 * Get token configuration from environment variables
 *
 * Environment variables:
 * - CLAUDE_MEMORY_TOKEN_BUDGET: Total token budget (default: 2500)
 * - CLAUDE_MEMORY_TIER: Default retrieval tier (summary|compact|full, default: compact)
 * - CLAUDE_MEMORY_AUTO_ADJUST: Auto-adjust tier if over budget (true|false, default: true)
 */
export function getTokenConfig(overrides?: Partial<TokenConfig>): TokenConfig {
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
  if (typeof config.autoAdjust !== 'boolean') throw new TypeError('autoAdjust must be a boolean');
  requireObject(config.budget, 'budget');
  requireInteger(config.budget.total, 'budget.total', 1);
  requireObject(config.budget.allocated, 'budget.allocated');
  let remaining = config.budget.total;
  for (const key of ['patterns', 'incidents', 'metadata'] as const) {
    const allocation = config.budget.allocated[key];
    requireInteger(allocation, `budget.allocated.${key}`, 0);
    // Subtract rather than summing, so even safe integers cannot overflow the sum.
    if (allocation > remaining) throw new TypeError('budget allocations must not exceed total');
    remaining -= allocation;
  }
  requireObject(config.budget.perItem, 'budget.perItem');
  for (const key of ['pattern', 'incident', 'summary'] as const) {
    requireInteger(config.budget.perItem[key], `budget.perItem.${key}`, 1);
  }

  return { ...config, defaultTier: config.defaultTier, autoAdjust: config.autoAdjust };
}

/**
 * Display token configuration
 */
export function displayTokenConfig(config?: TokenConfig): void {
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
export function getLogPaths() {
  return {
    root: path.join(process.cwd(), '.build-loop', 'logs', 'debugging'),
    operations: path.join(process.cwd(), '.build-loop', 'logs', 'debugging', 'operations.jsonl'),
    errors: path.join(process.cwd(), '.build-loop', 'logs', 'debugging', 'errors.jsonl'),
  };
}

/**
 * Get paths for trace storage
 */
export function getTracePaths(config?: MemoryConfig) {
  const cfg = config || getConfig();

  return {
    traces: path.join(cfg.memoryPath, 'traces'),
    index: path.join(cfg.memoryPath, 'traces', 'index.json'),
    collections: path.join(cfg.memoryPath, 'traces', 'collections'),
    raw: path.join(cfg.memoryPath, 'traces', 'raw'),
    correlations: path.join(cfg.memoryPath, 'correlations'),
  };
}
