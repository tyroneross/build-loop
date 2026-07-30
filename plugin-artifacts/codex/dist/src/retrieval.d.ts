/**
 * Debugging Memory System - Retrieval Layer
 *
 * Finds similar incidents and patterns based on symptom description.
 * Uses multi-strategy search: exact → tag → fuzzy → semantic (optional).
 */
import type { Incident, Pattern, RetrievalConfig, RetrievalResult, MemoryConfig } from './types';
/**
 * Enhanced search result with match type and highlights
 */
export interface SearchResult {
    incident: Incident;
    score: number;
    matchType: 'exact' | 'tag' | 'fuzzy' | 'category' | 'semantic';
    highlights: string[];
}
/**
 * Check memory for similar incidents
 *
 * This is the main entry point for memory retrieval.
 */
export declare function checkMemory(symptom: string, config?: Partial<RetrievalConfig> & {
    memoryConfig?: MemoryConfig;
}): Promise<RetrievalResult>;
/**
 * Get full incident details (for lazy loading)
 */
export declare function getFullIncident(incident_id: string): Promise<Incident | null>;
/**
 * Simple search by tags
 */
export declare function searchByTags(tags: string[], config?: MemoryConfig): Promise<Incident[]>;
/**
 * Get recent incidents
 */
export declare function getRecentIncidents(days?: number, config?: MemoryConfig): Promise<Incident[]>;
/**
 * Enhanced multi-strategy search
 *
 * Strategy order: exact → tag → fuzzy → category
 * Each strategy adds results with appropriate confidence scores
 */
export declare function enhancedSearch(query: string, options?: {
    threshold?: number;
    maxResults?: number;
    memoryConfig?: MemoryConfig;
}): Promise<SearchResult[]>;
import type { TieredRetrievalConfig, TieredRetrievalResult, SearchVerdict, VerdictResult, ProgressiveResult } from './types';
/**
 * Token-optimized memory check with tiered retrieval
 *
 * Tiers:
 * - 'summary': Minimal representation (~100 tokens/incident) for quick scans
 * - 'compact': Short keys, truncated fields (~200 tokens/incident) - DEFAULT
 * - 'full': Complete incident data (~550 tokens/incident) for detailed analysis
 *
 * Token budget enforcement ensures context doesn't exceed limits.
 */
export declare function checkMemoryTiered(symptom: string, config?: Partial<TieredRetrievalConfig> & {
    memoryConfig?: MemoryConfig;
}): Promise<TieredRetrievalResult>;
/**
 * Quick memory check with minimal output
 *
 * Returns only essential fields for rapid assessment.
 * Useful when token budget is very tight.
 */
export declare function quickMemoryCheck(symptom: string, config?: {
    memoryConfig?: MemoryConfig;
    maxResults?: number;
}): Promise<{
    hasMatches: boolean;
    matchCount: number;
    topMatch?: {
        id: string;
        symptom: string;
        category: string;
        confidence: number;
    };
    tokensUsed: number;
}>;
/**
 * Get incident details on demand (lazy loading)
 *
 * Use this when compact/summary tier found a match and full details are needed.
 */
export declare function getIncidentDetails(incident_id: string, config?: MemoryConfig): Promise<Incident | null>;
/**
 * Get pattern details on demand (lazy loading)
 */
export declare function getPatternDetails(pattern_id: string, config?: MemoryConfig): Promise<Pattern | null>;
/**
 * Estimate token usage for different retrieval tiers
 */
export declare function estimateTokensForTier(incidentCount: number, patternCount: number, tier: 'summary' | 'compact' | 'full'): number;
/**
 * Classify search results into an actionable verdict
 *
 * Verdicts:
 * - KNOWN_FIX:    High-confidence match with verified pattern (>0.8, verified)
 * - LIKELY_MATCH:  Good match with relevant incidents (0.5-0.8)
 * - WEAK_SIGNAL:   Possible relation, worth reviewing (0.3-0.5)
 * - NO_MATCH:      Nothing found, debug fresh
 */
export declare function classifyVerdict(result: RetrievalResult): SearchVerdict;
/**
 * Check memory and return verdict-wrapped results
 *
 * This is the recommended entry point for memory-first debugging.
 * Returns an actionable verdict instead of raw scores.
 */
export declare function checkMemoryWithVerdict(symptom: string, config?: Partial<RetrievalConfig> & {
    memoryConfig?: MemoryConfig;
}): Promise<VerdictResult>;
/**
 * Check memory with progressive depth — returns one-liner matches with drill-down commands
 *
 * Total output stays under 500 tokens even with 10 matches.
 * Use /debugger-detail <ID> to get full incident data on demand.
 */
export declare function checkMemoryProgressive(symptom: string, config?: Partial<RetrievalConfig> & {
    memoryConfig?: MemoryConfig;
}): Promise<ProgressiveResult>;
/**
 * Check memory using keyword index for scalable retrieval
 *
 * Instead of loading ALL incidents (O(n) file reads), this:
 * 1. Extracts query keywords
 * 2. Loads keyword index (1 file read)
 * 3. Finds candidate IDs via set intersection
 * 4. Loads only top candidates (max 20 file reads)
 * 5. Scores and ranks
 *
 * Falls back to checkMemory() if keyword index doesn't exist.
 */
export declare function checkMemoryScaled(symptom: string, config?: Partial<RetrievalConfig> & {
    memoryConfig?: MemoryConfig;
}): Promise<RetrievalResult>;
//# sourceMappingURL=retrieval.d.ts.map