/**
 * Debugging Memory System - Storage Layer
 *
 * Handles reading/writing incidents and patterns to filesystem.
 * File-based storage for simplicity and portability.
 * Now supports configurable paths (local or shared mode).
 */
import type { Incident, Pattern, StorageOptions, VerificationResult, MemoryConfig, MemoryIndex, IncidentLogEntry, KeywordIndex, VerdictOutcome } from './types';
/**
 * Store an incident in memory
 */
export declare function storeIncident(incident: Incident, options?: StorageOptions & {
    config?: MemoryConfig;
    interactive?: boolean;
}): Promise<{
    incident_id: string;
    file_path: string;
}>;
/**
 * Load an incident by ID
 */
export declare function loadIncident(incident_id: string, config?: MemoryConfig): Promise<Incident | null>;
/**
 * Load all incidents with batched I/O (max 50 concurrent reads)
 */
export declare function loadAllIncidents(config?: MemoryConfig): Promise<Incident[]>;
/**
 * Store a pattern
 */
export declare function storePattern(pattern: Pattern, config?: MemoryConfig): Promise<string>;
/**
 * Load a pattern by ID
 */
export declare function loadPattern(pattern_id: string, config?: MemoryConfig): Promise<Pattern | null>;
/**
 * Load all patterns with batched I/O (max 50 concurrent reads)
 */
export declare function loadAllPatterns(config?: MemoryConfig): Promise<Pattern[]>;
/**
 * Validate incident structure
 */
export declare function validateIncident(incident: Incident): VerificationResult;
/**
 * Generate incident ID with optional category prefix for self-documenting filenames
 *
 * Without category: INC_20250215_143022_a1b2
 * With category:    INC_API_20250215_143022_a1b2
 */
export declare function generateIncidentId(category?: string): string;
/**
 * Generate pattern ID
 */
export declare function generatePatternId(category: string, name: string): string;
/**
 * Get memory statistics
 */
export declare function getMemoryStats(config?: MemoryConfig): Promise<{
    total_incidents: number;
    total_patterns: number;
    oldest_incident: number;
    newest_incident: number;
    disk_usage_kb: number;
}>;
import type { CompactIncident, CompactPattern, IncidentSummary } from './types';
/**
 * Generate minimal incident summary (~100 tokens)
 */
export declare function generateIncidentSummary(incident: Incident): IncidentSummary;
/**
 * Convert full incident to compact format (~200 tokens)
 *
 * Uses short keys to minimize token usage in LLM context.
 * Preserves essential information for debugging assistance.
 */
export declare function toCompactIncident(incident: Incident): CompactIncident;
/**
 * Convert full pattern to compact format (~120 tokens)
 */
export declare function toCompactPattern(pattern: Pattern): CompactPattern;
/**
 * Batch convert incidents to compact format
 */
export declare function toCompactIncidents(incidents: Incident[]): CompactIncident[];
/**
 * Batch convert patterns to compact format
 */
export declare function toCompactPatterns(patterns: Pattern[]): CompactPattern[];
/**
 * Estimate tokens for a compact incident
 */
export declare function estimateCompactIncidentTokens(incident: CompactIncident): number;
/**
 * Estimate tokens for a compact pattern
 */
export declare function estimateCompactPatternTokens(pattern: CompactPattern): number;
/**
 * Enforce token budget on results
 *
 * Returns limited incidents and patterns that fit within budget.
 * Patterns get 30% budget, incidents get 60%, metadata gets 10%.
 */
export declare function enforceTokenBudget(incidents: CompactIncident[], patterns: CompactPattern[], budget?: number): {
    limitedIncidents: CompactIncident[];
    limitedPatterns: CompactPattern[];
    tokensUsed: number;
    truncated: {
        incidents: number;
        patterns: number;
    };
};
/**
 * Load incidents and return compact versions
 */
export declare function loadCompactIncidents(config?: MemoryConfig): Promise<CompactIncident[]>;
/**
 * Load patterns and return compact versions
 */
export declare function loadCompactPatterns(config?: MemoryConfig): Promise<CompactPattern[]>;
/**
 * Append incident entry to JSONL log for fast search
 */
export declare function appendToIncidentLog(incident: Incident, config?: MemoryConfig): Promise<void>;
/**
 * Search JSONL log for fast text matching (avoids loading all incident files)
 */
export declare function searchIncidentLog(query: string, config?: MemoryConfig): Promise<IncidentLogEntry[]>;
/**
 * Build or rebuild the memory index from all incidents
 */
export declare function rebuildIndex(config?: MemoryConfig): Promise<MemoryIndex>;
/**
 * Load memory index (fast, no incident file reads needed)
 */
export declare function loadIndex(config?: MemoryConfig): Promise<MemoryIndex | null>;
/**
 * Build a compact MEMORY_SUMMARY.md for LLM context injection
 * Stays under 150 lines to fit in context windows
 */
export declare function buildMemorySummary(config?: MemoryConfig): Promise<string>;
/**
 * Archive incidents older than maxAge days, keeping at most maxActive incidents
 */
export declare function archiveOldIncidents(options?: {
    maxActive?: number;
    maxAgeDays?: number;
    dryRun?: boolean;
}, config?: MemoryConfig): Promise<{
    archived: string[];
    kept: number;
}>;
/**
 * Generate compressed context string optimized for LLM consumption
 * Fits within token budget while maximizing information density
 */
export declare function compressContext(incidents: CompactIncident[], patterns: CompactPattern[], budget?: number): string;
/**
 * Update keyword index with a new incident (incremental, no full rebuild)
 */
export declare function updateKeywordIndex(incident: Incident, config?: MemoryConfig): Promise<void>;
/**
 * Load keyword index from disk
 */
export declare function loadKeywordIndex(config?: MemoryConfig): Promise<KeywordIndex | null>;
/**
 * Find candidate incident IDs by keyword intersection
 */
export declare function findCandidatesByKeyword(queryWords: string[], index: KeywordIndex, maxCandidates?: number): string[];
/**
 * Rebuild keyword index from scratch
 */
export declare function rebuildKeywordIndex(config?: MemoryConfig): Promise<KeywordIndex>;
/**
 * Update index incrementally with a new incident (avoids full loadAllIncidents)
 */
export declare function updateIndexIncremental(incident: Incident, config?: MemoryConfig): Promise<void>;
/**
 * Record whether a verdict suggestion actually worked
 */
export declare function recordOutcome(outcome: VerdictOutcome, config?: MemoryConfig): Promise<void>;
/**
 * Load all recorded outcomes
 */
export declare function loadOutcomes(config?: MemoryConfig): Promise<VerdictOutcome[]>;
/**
 * Get outcome statistics for a specific incident
 */
export declare function getOutcomeStats(incident_id: string, config?: MemoryConfig): Promise<{
    worked: number;
    failed: number;
    modified: number;
}>;
//# sourceMappingURL=storage.d.ts.map