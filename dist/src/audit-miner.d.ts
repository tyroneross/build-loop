/**
 * Audit Trail Miner
 *
 * Recovers debugging incidents from .claude/audit files when agents fail to store them manually.
 * This is the 85% fallback layer when wrapper functions aren't used or agents drop the ball.
 */
import type { Incident, MemoryConfig } from './types';
/**
 * Mine audit trail for incidents
 */
export declare function mineAuditTrail(options?: {
    days_back?: number;
    auto_store?: boolean;
    min_confidence?: number;
    config?: MemoryConfig;
}): Promise<Incident[]>;
/**
 * Dry run - show what would be mined
 */
export declare function previewAuditMining(days_back?: number, config?: MemoryConfig): Promise<void>;
//# sourceMappingURL=audit-miner.d.ts.map