/**
 * Interactive Verification System
 *
 * Guides users through completing incident details with interactive prompts.
 * Improves incident quality by ensuring all critical fields are filled.
 */
import type { Incident } from './types';
export { calculateQualityScore, generateQualityFeedback } from './quality';
/**
 * Build a complete incident using interactive prompts
 */
export declare function buildIncidentInteractive(baseIncident: Partial<Incident>): Promise<Incident>;
//# sourceMappingURL=interactive-verifier.d.ts.map