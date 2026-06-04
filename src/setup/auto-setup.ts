#!/usr/bin/env node
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

import * as fs from 'fs';
import * as path from 'path';
import { createSlashCommands } from './create-slash-commands';
import { configureHooks } from './configure-hooks';
import { injectClaudeMd } from './inject-claude-md';

/**
 * Find the project root
 */
function findProjectRoot(): string {
  // npm sets INIT_CWD to the original working directory
  if (process.env.INIT_CWD) {
    return process.env.INIT_CWD;
  }
  return process.cwd();
}

/**
 * Check if we should skip auto-setup
 */
function shouldSkip(): boolean {
  if (process.env.CI) return true;
  if (process.env.npm_config_global === 'true') return true;
  if (process.env.CLAUDE_MEMORY_SKIP_SETUP === 'true') return true;
  return false;
}

/**
 * Main auto-setup function
 */
async function autoSetup(): Promise<void> {
  if (shouldSkip()) return;

  const projectRoot = findProjectRoot();

  // Verify we found a real project
  const projectPkg = path.join(projectRoot, 'package.json');
  if (!fs.existsSync(projectPkg)) return;

  // Skip if this is our own package (development mode)
  try {
    const pkg = JSON.parse(fs.readFileSync(projectPkg, 'utf-8'));
    if (pkg.name === '@tyroneross/build-loop') return;
  } catch { /* ignore */ }

  const DIM = '\x1b[2m';
  const GREEN = '\x1b[32m';
  const CYAN = '\x1b[36m';
  const RESET = '\x1b[0m';

  console.log(`\n  ${GREEN}Build Loop${RESET} — Setting up native debugging memory\n`);

  const steps: string[] = [];

  try {
    // 1. Create memory directories
    const memoryPath = path.join(projectRoot, '.claude', 'memory');
    fs.mkdirSync(path.join(memoryPath, 'incidents'), { recursive: true });
    fs.mkdirSync(path.join(memoryPath, 'patterns'), { recursive: true });
    fs.mkdirSync(path.join(memoryPath, 'sessions'), { recursive: true });
    steps.push('Created .claude/memory/ directories');

    // 2. Create slash commands
    try {
      const commandsCreated = await createSlashCommands(projectRoot);
      if (commandsCreated > 0) {
        steps.push(`Installed ${commandsCreated} slash commands (/debugger, /debugger-detail, ...)`);
      }
    } catch { /* silently skip */ }

    // 3. Configure hooks
    try {
      const hooksConfigured = await configureHooks(projectRoot);
      if (hooksConfigured) {
        steps.push('Verified build-loop hook ownership');
      }
    } catch { /* silently skip */ }

    // 4. Inject into CLAUDE.md
    try {
      const claudeMdUpdated = await injectClaudeMd(projectRoot);
      if (claudeMdUpdated) {
        steps.push('Updated CLAUDE.md with debugging memory docs');
      }
    } catch { /* silently skip */ }

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

  } catch (error) {
    // Don't fail npm install
    console.warn(`  Setup skipped: ${(error as Error).message}\n`);
  }
}

// Run setup
autoSetup().catch(() => {
  // Silently fail — never break npm install
});
