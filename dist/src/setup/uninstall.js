"use strict";
/**
 * Uninstall - Clean removal of debugging memory from a project
 *
 * Removes:
 * 1. Hooks from .claude/settings.json
 * 2. Slash commands from .claude/commands/
 * 3. Debugging Memory section from CLAUDE.md
 * 4. Optionally: memory data (.claude/memory/)
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.uninstall = uninstall;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
/**
 * List of command files installed by the debugger
 */
const DEBUGGER_COMMANDS = [
    'debugger.md',
    'debugger-detail.md',
    'debugger-status.md',
    'debugger-scan.md',
    'assess.md',
    'feedback.md',
    'update.md',
];
/**
 * Remove debugger hooks from .claude/settings.json
 */
function removeHooks(projectRoot) {
    const settingsPath = path.join(projectRoot, '.claude', 'settings.json');
    if (!fs.existsSync(settingsPath))
        return false;
    try {
        const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
        if (!settings.hooks || typeof settings.hooks !== 'object')
            return false;
        let modified = false;
        const hooks = settings.hooks;
        // Remove Stop hooks containing our command
        if (Array.isArray(hooks.Stop)) {
            const before = hooks.Stop.length;
            hooks.Stop = hooks.Stop.filter((h) => {
                if (h.hooks) {
                    return !h.hooks.some((hook) => hook.command?.includes('claude-code-debugger'));
                }
                return !h.command?.includes('claude-code-debugger');
            });
            if (hooks.Stop.length < before)
                modified = true;
            if (hooks.Stop.length === 0)
                delete hooks.Stop;
        }
        // Remove SessionStart hooks containing our command
        if (Array.isArray(hooks.SessionStart)) {
            const before = hooks.SessionStart.length;
            hooks.SessionStart = hooks.SessionStart.filter((h) => {
                if (h.hooks) {
                    return !h.hooks.some((hook) => hook.command?.includes('claude-code-debugger') ||
                        hook.prompt?.includes('Debugging Memory'));
                }
                return !h.prompt?.includes('Debugging Memory');
            });
            if (hooks.SessionStart.length < before)
                modified = true;
            if (hooks.SessionStart.length === 0)
                delete hooks.SessionStart;
        }
        // Remove PreToolUse hooks mentioning debugging memory
        if (Array.isArray(hooks.PreToolUse)) {
            const before = hooks.PreToolUse.length;
            hooks.PreToolUse = hooks.PreToolUse.filter((h) => {
                if (h.hooks) {
                    return !h.hooks.some((hook) => hook.prompt?.includes('debugging memory'));
                }
                return !h.prompt?.includes('debugging memory');
            });
            if (hooks.PreToolUse.length < before)
                modified = true;
            if (hooks.PreToolUse.length === 0)
                delete hooks.PreToolUse;
        }
        // Remove PostToolUse hooks mentioning debugging
        if (Array.isArray(hooks.PostToolUse)) {
            const before = hooks.PostToolUse.length;
            hooks.PostToolUse = hooks.PostToolUse.filter((h) => {
                if (h.hooks) {
                    return !h.hooks.some((hook) => hook.prompt?.includes('debugging') || hook.prompt?.includes('debugger-scan'));
                }
                return !(h.prompt?.includes('debugging') || h.prompt?.includes('debugger-scan'));
            });
            if (hooks.PostToolUse.length < before)
                modified = true;
            if (hooks.PostToolUse.length === 0)
                delete hooks.PostToolUse;
        }
        // Clean up empty hooks object
        if (Object.keys(hooks).length === 0) {
            delete settings.hooks;
        }
        if (modified) {
            fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
        }
        return modified;
    }
    catch {
        return false;
    }
}
/**
 * Remove slash commands installed by the debugger
 */
function removeCommands(projectRoot) {
    const commandsDir = path.join(projectRoot, '.claude', 'commands');
    const removed = [];
    if (!fs.existsSync(commandsDir))
        return removed;
    for (const cmd of DEBUGGER_COMMANDS) {
        const cmdPath = path.join(commandsDir, cmd);
        if (fs.existsSync(cmdPath)) {
            try {
                // Verify it's ours by checking content
                const content = fs.readFileSync(cmdPath, 'utf-8');
                if (content.includes('debugger') || content.includes('claude-code-debugger') || content.includes('assess')) {
                    fs.unlinkSync(cmdPath);
                    removed.push(cmd);
                }
            }
            catch { /* skip */ }
        }
    }
    return removed;
}
/**
 * Remove Debugging Memory sections from CLAUDE.md
 */
function cleanClaudeMd(projectRoot) {
    const claudeMdPath = path.join(projectRoot, 'CLAUDE.md');
    if (!fs.existsSync(claudeMdPath))
        return false;
    try {
        let content = fs.readFileSync(claudeMdPath, 'utf-8');
        const originalLength = content.length;
        // Remove dynamic section
        content = content.replace(/<!-- debugger-dynamic-start -->[\s\S]*?<!-- debugger-dynamic-end -->\n*/g, '');
        // Remove static Debugging Memory section
        // Match from "## Debugging Memory" to the next ## heading or end of file
        content = content.replace(/\n*## Debugging Memory\n[\s\S]*?(?=\n## [^D]|\n## $|$)/g, '');
        // Remove v1.5.0 Storage Architecture section
        content = content.replace(/\n*## v1\.5\.0 Storage Architecture\n[\s\S]*?(?=\n## [^v]|\n## $|$)/g, '');
        // Remove Plugin Development section if it's ours
        content = content.replace(/\n*## Plugin Development\n[\s\S]*?claude-code-debugger[\s\S]*?(?=\n## [^P]|\n## $|$)/g, '');
        // Remove Architecture Patterns section if it's ours
        content = content.replace(/\n*## Architecture Patterns \(from IBR & NavGator\)\n[\s\S]*?(?=\n## [^A]|\n## $|$)/g, '');
        // Trim trailing whitespace
        content = content.trimEnd() + '\n';
        if (content.length !== originalLength) {
            fs.writeFileSync(claudeMdPath, content);
            return true;
        }
        return false;
    }
    catch {
        return false;
    }
}
/**
 * Remove memory data directory
 */
function removeMemoryData(projectRoot) {
    const memoryPath = path.join(projectRoot, '.claude', 'memory');
    if (!fs.existsSync(memoryPath))
        return false;
    try {
        fs.rmSync(memoryPath, { recursive: true, force: true });
        return true;
    }
    catch {
        return false;
    }
}
/**
 * Run full uninstall
 */
async function uninstall(projectRoot, options = {}) {
    const result = {
        hooksRemoved: false,
        commandsRemoved: [],
        claudeMdCleaned: false,
        memoryRemoved: false,
    };
    result.hooksRemoved = removeHooks(projectRoot);
    result.commandsRemoved = removeCommands(projectRoot);
    result.claudeMdCleaned = cleanClaudeMd(projectRoot);
    if (options.removeData) {
        result.memoryRemoved = removeMemoryData(projectRoot);
    }
    return result;
}
//# sourceMappingURL=uninstall.js.map