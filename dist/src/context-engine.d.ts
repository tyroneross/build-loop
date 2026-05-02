/**
 * Context Engine - Auto-injection, file-aware context, and session context
 *
 * Three layers of progressive context:
 * 1. CLAUDE.md dynamic section (~250 tokens, always present)
 * 2. Session-start context (compact JSON with stats + triggers)
 * 3. File-aware pre-tool context (incident IDs for files being edited)
 */
import type { MemoryConfig } from './types';
/**
 * Generate the dynamic CLAUDE.md section from index data
 *
 * Reads index.json (O(1), no file scan) and produces a ~250 token section
 * that tells the AI WHEN to invoke /debugger.
 */
export declare function generateDynamicSection(config?: MemoryConfig): Promise<string>;
/**
 * Generate session-start context — compact JSON for the SessionStart hook
 *
 * Output: ~150 tokens of structured context.
 */
export declare function generateSessionContext(config?: MemoryConfig): Promise<{
    ok: boolean;
    memory_state: {
        incidents: number;
        patterns: number;
        categories: string[];
        hot_files: string[];
        recent_ids: string[];
    };
    triggers: string[];
}>;
/**
 * Check if a file has past incidents — for PreToolUse file-aware hook
 *
 * Returns incident IDs if file has past bugs, or {ok: true} if clean.
 * Zero noise for files without history.
 */
export declare function checkFileContext(filepath: string, config?: MemoryConfig): Promise<{
    ok: boolean;
    has_incidents: boolean;
    incident_ids?: string[];
    message?: string;
}>;
//# sourceMappingURL=context-engine.d.ts.map