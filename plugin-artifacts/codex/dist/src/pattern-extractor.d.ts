/**
 * Pattern Extractor
 *
 * Automatically detects when 3+ similar incidents can be extracted into
 * a reusable pattern. Helps prevent solving the same problem repeatedly.
 */
import type { Incident, Pattern, MemoryConfig } from './types';
/**
 * Analyze incidents and extract reusable patterns
 */
export declare function extractPatterns(options?: {
    min_incidents?: number;
    min_similarity?: number;
    auto_store?: boolean;
    config?: MemoryConfig;
}): Promise<Pattern[]>;
/**
 * Auto-extract pattern if enough similar incidents exist
 * Called automatically after storing an incident
 */
export declare function autoExtractPatternIfReady(newIncident: Incident, options?: {
    minSimilar?: number;
    minQuality?: number;
    config?: MemoryConfig;
}): Promise<Pattern | null>;
/**
 * Suggest patterns to extract (dry run)
 */
export declare function suggestPatterns(config?: MemoryConfig): Promise<void>;
//# sourceMappingURL=pattern-extractor.d.ts.map