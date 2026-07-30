/**
 * Uninstall - Clean removal of debugging memory from a project
 *
 * Removes:
 * 1. Hooks from .claude/settings.json
 * 2. Slash commands from .claude/commands/
 * 3. Debugging Memory section from CLAUDE.md
 * 4. Optionally: memory data (.claude/memory/)
 */
export interface UninstallResult {
    hooksRemoved: boolean;
    commandsRemoved: string[];
    claudeMdCleaned: boolean;
    memoryRemoved: boolean;
}
/**
 * Run full uninstall
 */
export declare function uninstall(projectRoot: string, options?: {
    removeData?: boolean;
}): Promise<UninstallResult>;
//# sourceMappingURL=uninstall.d.ts.map