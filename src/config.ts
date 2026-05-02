/**
 * Configuration System
 *
 * Supports two modes:
 * - local: Each project has its own .claude/memory/ directory
 * - shared: All projects share ~/.claude-memory/ directory
 */

import * as path from 'path';
import * as os from 'os';

export interface MemoryConfig {
  /** Storage mode: local (per-project) or shared (global) */
  storageMode: 'local' | 'shared';

  /** Path to memory directory */
  memoryPath: string;

  /** Whether to automatically mine audit trail on startup */
  autoMine: boolean;

  /** Similarity threshold for retrieval (0-1) */
  defaultSimilarityThreshold: number;

  /** Maximum results to return */
  defaultMaxResults: number;
}

/**
 * Get configuration from environment variables and defaults
 */
export function getConfig(overrides?: Partial<MemoryConfig>): MemoryConfig {
  // Check environment variable for mode
  const mode = (process.env.CLAUDE_MEMORY_MODE as 'local' | 'shared') || 'local';

  // Determine memory path based on mode
  let memoryPath: string;

  if (mode === 'shared') {
    // Shared mode: Use home directory
    const customPath = process.env.CLAUDE_MEMORY_PATH;
    memoryPath = customPath || path.join(os.homedir(), '.claude-memory');
  } else {
    // Local mode: Use current working directory
    const customPath = process.env.CLAUDE_MEMORY_PATH;
    memoryPath = customPath || path.join(process.cwd(), '.claude/memory');
  }

  const config: MemoryConfig = {
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
  const totalBudget = parseInt(process.env.CLAUDE_MEMORY_TOKEN_BUDGET || '2500', 10);
  const tier = (process.env.CLAUDE_MEMORY_TIER as 'summary' | 'compact' | 'full') || 'compact';
  const autoAdjust = process.env.CLAUDE_MEMORY_AUTO_ADJUST !== 'false';

  const config: TokenConfig = {
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
 * Logs are stored under .claude-code-debugger/logs/ in the project root.
 * Separate from memory paths — these are operational logs, not debugging incidents.
 */
export function getLogPaths() {
  return {
    root: path.join(process.cwd(), '.claude-code-debugger', 'logs'),
    operations: path.join(process.cwd(), '.claude-code-debugger', 'logs', 'operations.jsonl'),
    errors: path.join(process.cwd(), '.claude-code-debugger', 'logs', 'errors.jsonl'),
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
