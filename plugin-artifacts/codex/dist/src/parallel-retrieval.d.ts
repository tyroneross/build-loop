/**
 * Parallel Retrieval System
 *
 * Runs all 4 retrieval strategies simultaneously using Promise.all.
 * Significantly faster than sequential execution (~4x speedup).
 *
 * Strategies:
 * - Exact Match (score: 1.0) - Substring matching in symptom
 * - Tag Match (score: 0.9) - Bidirectional keyword matching on tags
 * - Fuzzy Match (score: 0.7-0.85) - Jaro-Winkler distance for similar strings
 * - Category Match (score: 0.6) - Group similar categories
 */
import type { Pattern, ParallelSearchResult, ParallelRetrievalResult, MemoryConfig } from './types';
/**
 * Execute all retrieval strategies in parallel
 *
 * @param query - Search query (symptom description)
 * @param options - Configuration options
 * @returns Merged and ranked results from all strategies
 */
export declare function parallelSearch(query: string, options?: {
    threshold?: number;
    maxResults?: number;
    memoryConfig?: MemoryConfig;
}): Promise<ParallelRetrievalResult>;
/**
 * Match patterns in parallel
 *
 * Scores all patterns simultaneously using Promise.all
 */
export declare function parallelPatternMatch(symptom: string, options?: {
    memoryConfig?: MemoryConfig;
    threshold?: number;
    maxResults?: number;
}): Promise<Pattern[]>;
/**
 * Combined parallel search with patterns and incidents
 *
 * Runs pattern matching and incident search in parallel,
 * then merges results with priority to patterns.
 */
export declare function parallelMemoryCheck(symptom: string, options?: {
    threshold?: number;
    maxResults?: number;
    memoryConfig?: MemoryConfig;
}): Promise<{
    patterns: Pattern[];
    incidents: ParallelSearchResult[];
    execution_time_ms: number;
    parallel_speedup: number;
}>;
//# sourceMappingURL=parallel-retrieval.d.ts.map