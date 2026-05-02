/**
 * Configuration System
 *
 * Supports two modes:
 * - local: Each project has its own .claude/memory/ directory
 * - shared: All projects share ~/.claude-memory/ directory
 */
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
export declare function getConfig(overrides?: Partial<MemoryConfig>): MemoryConfig;
/**
 * Get paths for different memory components
 */
export declare function getMemoryPaths(config?: MemoryConfig): {
    incidents: string;
    patterns: string;
    sessions: string;
    root: string;
};
/**
 * Display current configuration
 */
export declare function displayConfig(config?: MemoryConfig): void;
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
export declare function getTokenConfig(overrides?: Partial<TokenConfig>): TokenConfig;
/**
 * Display token configuration
 */
export declare function displayTokenConfig(config?: TokenConfig): void;
/**
 * Get paths for internal logger storage
 *
 * Logs are stored under .claude-code-debugger/logs/ in the project root.
 * Separate from memory paths — these are operational logs, not debugging incidents.
 */
export declare function getLogPaths(): {
    root: string;
    operations: string;
    errors: string;
};
/**
 * Get paths for trace storage
 */
export declare function getTracePaths(config?: MemoryConfig): {
    traces: string;
    index: string;
    collections: string;
    raw: string;
    correlations: string;
};
//# sourceMappingURL=config.d.ts.map