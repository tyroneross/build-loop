/**
 * Debug With Memory - Wrapper Function
 *
 * Automatically checks memory before debugging and stores incidents after.
 * Works with all 4 entry points: chief agent, direct agent, chat mode, resume work.
 */
import type { Incident, RetrievalResult } from './types';
interface DebugContext {
    symptom: string;
    memory: RetrievalResult;
    session_id: string;
    started_at: number;
}
interface DebugResult {
    success: boolean;
    incident?: Incident;
    memory_stored: boolean;
    verification_passed: boolean;
    context_used: DebugContext;
}
/**
 * Main wrapper function for debugging with memory
 *
 * Usage:
 *   const result = await debugWithMemory("Search filters not working");
 *
 * This function:
 * 1. Checks memory for similar past incidents
 * 2. Provides context to the debugging process
 * 3. Stores the incident after debugging
 * 4. Verifies storage succeeded
 */
export declare function debugWithMemory(symptom: string, options?: {
    agent?: string;
    auto_store?: boolean;
    min_confidence?: number;
}): Promise<DebugResult>;
/**
 * Store incident after debugging is complete
 *
 * Usage:
 *   await storeDebugIncident(session_id, {
 *     root_cause: { description: "...", confidence: 0.9 },
 *     fix: { approach: "...", changes: [...] },
 *     // ... other fields
 *   });
 */
export declare function storeDebugIncident(session_id: string, incident_data: Partial<Incident>): Promise<{
    success: boolean;
    incident_id: string;
    verified: boolean;
}>;
/**
 * Get memory system statistics
 */
export declare function getMemoryStatus(): Promise<void>;
export {};
//# sourceMappingURL=debug-wrapper.d.ts.map