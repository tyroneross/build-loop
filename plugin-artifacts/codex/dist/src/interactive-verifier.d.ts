/**
 * Interactive Verification System
 *
 * Guides users through completing incident details with interactive prompts.
 * Improves incident quality by ensuring all critical fields are filled.
 */
import type { Incident } from './types';
/**
 * Build a complete incident using interactive prompts
 */
export declare function buildIncidentInteractive(baseIncident: Partial<Incident>): Promise<Incident>;
/**
 * Calculate overall quality score for an incident
 *
 * Scoring rubric:
 * - Root cause analysis: 30% (description length + confidence)
 * - Fix details: 30% (approach + changes documented)
 * - Verification: 20% (verification status)
 * - Documentation: 20% (tags + prevention advice)
 */
export declare function calculateQualityScore(incident: Incident | Partial<Incident>): number;
/**
 * Generate quality feedback text
 */
export declare function generateQualityFeedback(incident: Incident): string;
//# sourceMappingURL=interactive-verifier.d.ts.map