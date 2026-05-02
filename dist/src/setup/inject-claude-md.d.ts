/**
 * Inject debugging memory documentation into CLAUDE.md
 *
 * Generates a dynamic section from index.json that includes:
 * - Current memory stats (O(1) read)
 * - Trigger instructions for when AI should call /debugger
 * - Category/file summary from index
 */
export declare function injectClaudeMd(projectRoot: string): Promise<boolean>;
//# sourceMappingURL=inject-claude-md.d.ts.map