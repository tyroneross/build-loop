#!/usr/bin/env node
"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Auto-Setup Script
 *
 * Legacy auto-setup helper for build-loop native debugging memory.
 * Build-loop no longer runs package-install setup hooks.
 *
 * What it does:
 * 1. Creates memory directories (.claude/memory/)
 * 2. Installs slash commands (/debugger, /debugger-detail, etc.)
 * 3. Relies on build-loop's packaged hooks/hooks.json
 * 4. Adds Debugging Memory section to CLAUDE.md
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
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const create_slash_commands_1 = require("./create-slash-commands");
const configure_hooks_1 = require("./configure-hooks");
const inject_claude_md_1 = require("./inject-claude-md");
/**
 * Find the project root
 */
function findProjectRoot() {
    // npm sets INIT_CWD to the original working directory
    if (process.env.INIT_CWD) {
        return process.env.INIT_CWD;
    }
    return process.cwd();
}
/**
 * Check if we should skip auto-setup
 */
function shouldSkip() {
    if (process.env.CI)
        return true;
    if (process.env.npm_config_global === 'true')
        return true;
    if (process.env.CLAUDE_MEMORY_SKIP_SETUP === 'true')
        return true;
    return false;
}
/**
 * Main auto-setup function
 */
async function autoSetup() {
    if (shouldSkip())
        return;
    const projectRoot = findProjectRoot();
    // Verify we found a real project
    const projectPkg = path.join(projectRoot, 'package.json');
    if (!fs.existsSync(projectPkg))
        return;
    // Skip if this is our own package (development mode)
    try {
        const pkg = JSON.parse(fs.readFileSync(projectPkg, 'utf-8'));
        if (pkg.name === '@tyroneross/build-loop')
            return;
    }
    catch { /* ignore */ }
    const DIM = '\x1b[2m';
    const GREEN = '\x1b[32m';
    const CYAN = '\x1b[36m';
    const RESET = '\x1b[0m';
    console.log(`\n  ${GREEN}Build Loop${RESET} — Setting up native debugging memory\n`);
    const steps = [];
    try {
        // 1. Create memory directories
        const memoryPath = path.join(projectRoot, '.claude', 'memory');
        fs.mkdirSync(path.join(memoryPath, 'incidents'), { recursive: true });
        fs.mkdirSync(path.join(memoryPath, 'patterns'), { recursive: true });
        fs.mkdirSync(path.join(memoryPath, 'sessions'), { recursive: true });
        steps.push('Created .claude/memory/ directories');
        // 2. Create slash commands
        try {
            const commandsCreated = await (0, create_slash_commands_1.createSlashCommands)(projectRoot);
            if (commandsCreated > 0) {
                steps.push(`Installed ${commandsCreated} slash commands (/debugger, /debugger-detail, ...)`);
            }
        }
        catch { /* silently skip */ }
        // 3. Configure hooks
        try {
            const hooksConfigured = await (0, configure_hooks_1.configureHooks)(projectRoot);
            if (hooksConfigured) {
                steps.push('Verified build-loop hook ownership');
            }
        }
        catch { /* silently skip */ }
        // 4. Inject into CLAUDE.md
        try {
            const claudeMdUpdated = await (0, inject_claude_md_1.injectClaudeMd)(projectRoot);
            if (claudeMdUpdated) {
                steps.push('Updated CLAUDE.md with debugging memory docs');
            }
        }
        catch { /* silently skip */ }
        // Print what happened
        for (const step of steps) {
            console.log(`  ${GREEN}+${RESET} ${step}`);
        }
        console.log();
        console.log(`  ${GREEN}Ready.${RESET} Debugging memory is now active.`);
        console.log();
        console.log(`  ${DIM}How it works:${RESET}`);
        console.log(`  ${DIM}  - Debug bugs as usual — fixes get stored automatically${RESET}`);
        console.log(`  ${DIM}  - Next time a similar bug appears, Claude checks memory first${RESET}`);
        console.log(`  ${DIM}  - Use${RESET} ${CYAN}/debugger "symptom"${RESET} ${DIM}to search manually${RESET}`);
        console.log();
        console.log(`  ${DIM}Use${RESET} /build-loop:debug ${DIM}for native deep debugging${RESET}`);
        console.log();
    }
    catch (error) {
        // Don't fail npm install
        console.warn(`  Setup skipped: ${error.message}\n`);
    }
}
// Run setup
autoSetup().catch(() => {
    // Silently fail — never break npm install
});
//# sourceMappingURL=auto-setup.js.map