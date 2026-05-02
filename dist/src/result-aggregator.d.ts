/**
 * Result Aggregator
 *
 * Combines and ranks results from multiple sources:
 * - Domain assessments from parallel assessors
 * - Memory retrieval (incidents and patterns)
 * - Trace correlations
 *
 * Uses unified scoring: 0.35*match + 0.25*confidence + 0.15*recency + 0.25*verification
 */
import type { DomainAssessment, AggregatedResult, CompactIncident, CompactPattern, ScoredItem } from './types';
export type { ScoredItem } from './types';
export interface AggregationConfig {
    maxResults?: number;
    minScoreThreshold?: number;
    verificationBoost?: number;
    recencyBoost?: number;
    dedupeThreshold?: number;
}
/**
 * Score a domain assessment
 */
export declare function scoreAssessment(assessment: DomainAssessment, config?: AggregationConfig): ScoredItem;
/**
 * Score a compact incident from memory
 */
export declare function scoreIncident(incident: CompactIncident, config?: AggregationConfig): ScoredItem;
/**
 * Score a compact pattern from memory
 */
export declare function scorePattern(pattern: CompactPattern, matchScore?: number, config?: AggregationConfig): ScoredItem;
/**
 * Aggregate results from multiple sources
 */
export declare function aggregateResults(assessments: DomainAssessment[], incidents: CompactIncident[], patterns: CompactPattern[], config?: AggregationConfig): AggregatedResult;
/**
 * Format aggregated results for display
 */
export declare function formatAggregatedResults(result: AggregatedResult): string;
/**
 * Create a quick summary for token-efficient output
 */
export declare function createQuickSummary(result: AggregatedResult): string;
//# sourceMappingURL=result-aggregator.d.ts.map