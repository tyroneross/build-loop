/**
 * Assessment Orchestrator
 *
 * Coordinates parallel domain-specific assessments for comprehensive debugging.
 * Detects which domains are involved based on symptom keywords and launches
 * appropriate assessors in parallel.
 */
import type { DomainAssessment, OrchestrationResult } from './types';
export type Domain = 'database' | 'frontend' | 'api' | 'performance';
export type DomainPriority = 'high' | 'medium' | 'low';
export interface DomainDetection {
    domain: Domain;
    priority: DomainPriority;
    matchCount: number;
    matchedKeywords: string[];
}
export interface OrchestrationConfig {
    minMatchThreshold?: number;
    maxParallelDomains?: number;
    includeLowPriority?: boolean;
    domainWeights?: Partial<Record<Domain, number>>;
}
/**
 * Detect which domains are potentially involved based on symptom keywords
 */
export declare function detectDomains(symptom: string, config?: OrchestrationConfig): DomainDetection[];
/**
 * Select which domains should be assessed based on detection results
 */
export declare function selectDomainsForAssessment(detections: DomainDetection[], config?: OrchestrationConfig): Domain[];
/**
 * Format domain detection results for the orchestrator agent
 */
export declare function formatDetectionForAgent(detections: DomainDetection[]): string;
/**
 * Generate prompts for each domain assessor agent
 */
export declare function generateAssessorPrompts(symptom: string, domains: Domain[]): Record<Domain, string>;
/**
 * Parse assessment JSON from agent response
 */
export declare function parseAssessmentResponse(response: string, domain: Domain): DomainAssessment | null;
/**
 * Rank assessments by confidence and evidence
 */
export declare function rankAssessments(assessments: DomainAssessment[]): DomainAssessment[];
/**
 * Generate priority ranking for orchestration result
 */
export declare function generatePriorityRanking(rankedAssessments: DomainAssessment[]): Array<{
    rank: number;
    domain: Domain;
    action: string;
}>;
/**
 * Generate recommended sequence of actions
 */
export declare function generateRecommendedSequence(rankedAssessments: DomainAssessment[]): string[];
/**
 * Create the final orchestration result
 */
export declare function createOrchestrationResult(symptom: string, assessments: DomainAssessment[]): OrchestrationResult;
/**
 * Format orchestration result for display
 */
export declare function formatOrchestrationResult(result: OrchestrationResult): string;
/**
 * Main orchestration entry point
 * Note: This prepares the orchestration but doesn't spawn agents directly.
 * The actual agent spawning is done by Claude Code using the Task tool.
 */
export declare function prepareOrchestration(symptom: string, config?: OrchestrationConfig): {
    detections: DomainDetection[];
    selectedDomains: Domain[];
    prompts: Record<Domain, string>;
    shouldUseOrchestrator: boolean;
};
//# sourceMappingURL=assessment-orchestrator.d.ts.map