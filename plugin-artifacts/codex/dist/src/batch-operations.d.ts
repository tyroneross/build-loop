/**
 * Batch Operations
 *
 * Batch commands for maintaining the memory system:
 * - Review incomplete incidents
 * - Extract patterns from existing incidents
 * - Clean up old sessions
 */
import type { Pattern, MemoryConfig } from './types';
/**
 * Review and complete incomplete incidents interactively
 */
export declare function batchReviewIncomplete(config?: MemoryConfig): Promise<void>;
/**
 * Extract patterns from existing incidents in batch
 */
export declare function batchExtractPatterns(options?: {
    category?: string;
    minIncidents?: number;
    config?: MemoryConfig;
}): Promise<Pattern[]>;
/**
 * Clean up old sessions and empty incidents
 */
export declare function batchCleanup(options?: {
    olderThanDays?: number;
    dryRun?: boolean;
    config?: MemoryConfig;
}): Promise<void>;
//# sourceMappingURL=batch-operations.d.ts.map