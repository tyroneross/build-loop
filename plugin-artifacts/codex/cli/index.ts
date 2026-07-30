#!/usr/bin/env node
/**
 * Claude Code Debugger - CLI Interface
 *
 * Command-line tool for debugging memory system
 */

import { Command } from 'commander';
import { version } from '../package.json';
import { debugWithMemory, storeDebugIncident, getMemoryStatus } from '../src/debug-wrapper';
import { suggestPatterns, extractPatterns } from '../src/pattern-extractor';
import { previewAuditMining, mineAuditTrail } from '../src/audit-miner';
import { displayConfig } from '../src/config';
import { checkMemory } from '../src/retrieval';
import {
  batchReviewIncomplete,
  batchExtractPatterns,
  batchCleanup
} from '../src/batch-operations';
import { loadIncident, loadPattern, recordOutcome, rebuildKeywordIndex } from '../src/storage';
import { generateSessionContext, checkFileContext } from '../src/context-engine';
import { uninstall } from '../src/setup/uninstall';
import { runInit } from './init';
import { addLesson, listLessons, type Lesson } from '../src/lessons';

const program = new Command();

function printQuickStart(): void {
  console.log('┌─────────────────────────────────────────────┐');
  console.log('│         🧠 Quick Start Guide                │');
  console.log('├─────────────────────────────────────────────┤');
  console.log('│                                             │');
  console.log('│  Hit a bug? Run /debugger "error msg"       │');
  console.log('│  to search past fixes before investigating. │');
  console.log('│                                             │');
  console.log('│  Fixes are saved automatically at session   │');
  console.log('│  end — the more you debug, the smarter it   │');
  console.log('│  gets. Use /debugger-status to see stats.   │');
  console.log('│                                             │');
  console.log('│  /debugger "symptom"  → search past bugs    │');
  console.log('│  /debugger-status     → memory stats        │');
  console.log('│  /debugger-scan       → mine past sessions  │');
  console.log('│  /update              → check for updates   │');
  console.log('│                                             │');
  console.log('└─────────────────────────────────────────────┘');
}

program
  .name('claude-code-debugger')
  .description('Debugging memory system - never solve the same bug twice')
  .version(version);

// Debug command
program
  .command('debug <symptom>')
  .description('Check memory for similar incidents before debugging')
  .option('--shared', 'Use shared memory mode')
  .option('--threshold <number>', 'Similarity threshold (0-1)', '0.5')
  .action(async (symptom: string, options: { shared?: boolean; threshold?: string }) => {
    try {
      if (options.shared) {
        process.env.CLAUDE_MEMORY_MODE = 'shared';
      }

      const result = await debugWithMemory(symptom, {
        min_confidence: parseFloat(options.threshold || '0.5')
      });

      console.log('\n📝 Session ID:', result.context_used.session_id);
      console.log('\nAfter fixing, run: claude-code-debugger store\n');

    } catch (error: any) {
      console.error('❌ Error:', error.message);
      process.exit(1);
    }
  });

// Status command
program
  .command('status')
  .description('Show memory statistics')
  .option('--shared', 'Use shared memory mode')
  .action(async (options: { shared?: boolean }) => {
    try {
      if (options.shared) {
        process.env.CLAUDE_MEMORY_MODE = 'shared';
      }

      await getMemoryStatus();
    } catch (error: any) {
      console.error('❌ Error:', error.message);
      process.exit(1);
    }
  });

// Config command
program
  .command('config')
  .description('Show current configuration')
  .action(() => {
    displayConfig();
  });

// Search command
program
  .command('search <query>')
  .description('Search memory for incidents')
  .option('--shared', 'Use shared memory mode')
  .option('--threshold <number>', 'Similarity threshold', '0.5')
  .action(async (query: string, options: { shared?: boolean; threshold?: string }) => {
    try {
      if (options.shared) {
        process.env.CLAUDE_MEMORY_MODE = 'shared';
      }

      const result = await checkMemory(query, {
        similarity_threshold: parseFloat(options.threshold || '0.5')
      });

      console.log(`\n📊 Found ${result.incidents.length} incidents:\n`);

      result.incidents.forEach((inc, i) => {
        console.log(`${i + 1}. ${inc.symptom?.substring(0, 60) ?? 'Unknown'}...`);
        console.log(`   Match: ${((inc.similarity_score ?? 0) * 100).toFixed(0)}%`);
        console.log(`   Fix: ${inc.fix?.approach?.substring(0, 60) ?? 'Unknown'}...\n`);
      });

    } catch (error: any) {
      console.error('❌ Error:', error.message);
      process.exit(1);
    }
  });

// Pattern commands
program
  .command('patterns')
  .description('Suggest patterns to extract')
  .option('--shared', 'Use shared memory mode')
  .option('--extract', 'Extract and store patterns')
  .action(async (options: { shared?: boolean; extract?: boolean }) => {
    try {
      if (options.shared) {
        process.env.CLAUDE_MEMORY_MODE = 'shared';
      }

      if (options.extract) {
        await extractPatterns({
          min_incidents: 3,
          min_similarity: 0.5,
          auto_store: true
        });
      } else {
        await suggestPatterns();
      }

    } catch (error: any) {
      console.error('❌ Error:', error.message);
      process.exit(1);
    }
  });

// Mine command
program
  .command('mine')
  .description('Mine audit trail for incidents')
  .option('--shared', 'Use shared memory mode')
  .option('--days <number>', 'Days to look back', '30')
  .option('--store', 'Store mined incidents')
  .action(async (options: { shared?: boolean; days?: string; store?: boolean }) => {
    try {
      if (options.shared) {
        process.env.CLAUDE_MEMORY_MODE = 'shared';
      }

      const days = parseInt(options.days || '30', 10);

      if (options.store) {
        await mineAuditTrail({
          days_back: days,
          auto_store: true,
          min_confidence: 0.7
        });
      } else {
        await previewAuditMining(days);
      }

    } catch (error: any) {
      console.error('❌ Error:', error.message);
      process.exit(1);
    }
  });

// Batch operations command
program
  .command('batch')
  .description('Batch operations for maintenance')
  .option('--incomplete', 'Review incomplete incidents interactively')
  .option('--extract-patterns', 'Extract patterns from existing incidents')
  .option('--cleanup', 'Clean up old sessions and low-quality incidents')
  .option('--category <category>', 'Filter by category (for pattern extraction)')
  .option('--min-incidents <number>', 'Minimum incidents for pattern (default: 3)', '3')
  .option('--older-than <days>', 'Age threshold for cleanup (default: 90)', '90')
  .option('--dry-run', 'Preview cleanup without making changes')
  .option('--shared', 'Use shared memory mode')
  .action(async (options: {
    incomplete?: boolean;
    extractPatterns?: boolean;
    cleanup?: boolean;
    category?: string;
    minIncidents?: string;
    olderThan?: string;
    dryRun?: boolean;
    shared?: boolean;
  }) => {
    try {
      if (options.shared) {
        process.env.CLAUDE_MEMORY_MODE = 'shared';
      }

      // Determine config
      const config = undefined; // Will use default from env

      // Run requested batch operations
      if (options.incomplete) {
        await batchReviewIncomplete(config);
      }

      if (options.extractPatterns) {
        const patterns = await batchExtractPatterns({
          category: options.category,
          minIncidents: parseInt(options.minIncidents || '3', 10),
          config
        });
        console.log(`✨ Extracted ${patterns.length} patterns`);
      }

      if (options.cleanup) {
        await batchCleanup({
          olderThanDays: parseInt(options.olderThan || '90', 10),
          dryRun: options.dryRun,
          config
        });
      }

      // If no options specified, show help
      if (!options.incomplete && !options.extractPatterns && !options.cleanup) {
        console.log('\n💡 Batch Operations');
        console.log('═══════════════════════════════════════════════\n');
        console.log('Available batch operations:\n');
        console.log('  --incomplete         Review incomplete incidents interactively');
        console.log('  --extract-patterns   Extract patterns from similar incidents');
        console.log('  --cleanup            Clean up old sessions and low-quality incidents\n');
        console.log('Examples:\n');
        console.log('  claude-code-debugger batch --incomplete');
        console.log('  claude-code-debugger batch --extract-patterns --category react-hooks');
        console.log('  claude-code-debugger batch --cleanup --older-than 90 --dry-run\n');
      }

    } catch (error: any) {
      console.error('❌ Error:', error.message);
      process.exit(1);
    }
  });

// Feedback command
program
  .command('feedback')
  .description('Submit feedback or report issues')
  .option('--bug', 'Report a bug')
  .option('--feature', 'Request a feature')
  .action(async (options: { bug?: boolean; feature?: boolean }) => {
    const { exec } = await import('child_process');

    console.log('\n💬 Claude Code Debugger - Feedback');
    console.log('═══════════════════════════════════════════════\n');

    // Determine issue type
    let issueType = 'feedback';
    let labels = 'feedback';
    let title = '';
    let body = '';

    if (options.bug) {
      issueType = 'bug';
      labels = 'bug';
      title = encodeURIComponent('[Bug] ');
      body = encodeURIComponent(`## Description
Describe the bug...

## Steps to Reproduce
1.
2.
3.

## Expected Behavior


## Actual Behavior


## Environment
- Version: ${version}
- Node: ${process.version}
- OS: ${process.platform}
`);
    } else if (options.feature) {
      issueType = 'feature';
      labels = 'enhancement';
      title = encodeURIComponent('[Feature] ');
      body = encodeURIComponent(`## Feature Request
Describe the feature...

## Use Case
Why is this needed?

## Proposed Solution

`);
    } else {
      body = encodeURIComponent(`## Feedback
Your feedback here...

---
Version: ${version}
`);
    }

    const url = `https://github.com/tyroneross/claude-code-debugger/issues/new?labels=${labels}&title=${title}&body=${body}`;

    console.log(`   Opening GitHub to submit ${issueType}...\n`);

    // Open browser (cross-platform)
    const openCommand = process.platform === 'darwin' ? 'open' :
                        process.platform === 'win32' ? 'start' : 'xdg-open';

    exec(`${openCommand} "${url}"`, (error) => {
      if (error) {
        console.log('   Could not open browser automatically.');
        console.log(`   Please visit: ${url}\n`);
      } else {
        console.log('   ✅ Browser opened! Create your issue on GitHub.\n');
      }
    });
  });

// Setup command - re-run auto-setup to sync commands, hooks, and CLAUDE.md
program
  .command('setup')
  .description('Re-run setup to sync slash commands, hooks, and CLAUDE.md')
  .option('--force', 'Force overwrite existing files')
  .action(async (options: { force?: boolean }) => {
    const fs = await import('fs');
    const pathMod = await import('path');

    console.log('\n🔧 Claude Code Debugger - Setup');
    console.log('═══════════════════════════════════════════════\n');

    const projectRoot = process.env.INIT_CWD || process.cwd();

    try {
      // 1. Create memory directories
      const memoryPath = pathMod.join(projectRoot, '.claude', 'memory');
      fs.mkdirSync(pathMod.join(memoryPath, 'incidents'), { recursive: true });
      fs.mkdirSync(pathMod.join(memoryPath, 'patterns'), { recursive: true });
      fs.mkdirSync(pathMod.join(memoryPath, 'sessions'), { recursive: true });
      console.log('   ✓ Memory directories ready');

      // 2. Sync slash commands from package
      const { createSlashCommands } = await import('../src/setup/create-slash-commands');
      try {
        const commandsCreated = await createSlashCommands(projectRoot, options.force);
        if (commandsCreated > 0) {
          console.log(`   ✓ Synced ${commandsCreated} slash commands`);
        } else {
          console.log('   ✓ Slash commands up to date');
        }
      } catch {
        console.log('   ⚠ Could not sync slash commands');
      }

      // 3. Configure hooks
      const { configureHooks } = await import('../src/setup/configure-hooks');
      try {
        const hooksConfigured = await configureHooks(projectRoot);
        if (hooksConfigured) {
          console.log('   ✓ Hooks configured');
        } else {
          console.log('   ✓ Hooks up to date');
        }
      } catch {
        console.log('   ⚠ Could not configure hooks');
      }

      // 4. Inject into CLAUDE.md
      const { injectClaudeMd } = await import('../src/setup/inject-claude-md');
      try {
        const claudeMdUpdated = await injectClaudeMd(projectRoot);
        if (claudeMdUpdated) {
          console.log('   ✓ Updated CLAUDE.md');
        } else {
          console.log('   ✓ CLAUDE.md up to date');
        }
      } catch {
        console.log('   ⚠ Could not update CLAUDE.md');
      }

      console.log('\n✅ Setup complete! Debugging memory is active.\n');
      printQuickStart();

    } catch (error: any) {
      console.error('❌ Setup failed:', error.message);
      process.exit(1);
    }
  });

// Update command
program
  .command('update')
  .description('Check for updates and install the latest version via npx')
  .option('-y, --yes', 'Skip confirmation prompt and install immediately')
  .action(async (options: { yes?: boolean }) => {
    const { execSync } = await import('child_process');
    const prompts = await import('prompts');

    console.log('\n🔄 Claude Code Debugger - Update Check');
    console.log('═══════════════════════════════════════════════\n');

    try {
      // Get current version
      console.log(`   Current version: ${version}`);

      // Check latest version from npm registry
      const pkgInfoRaw = execSync('npm view @tyroneross/claude-code-debugger --json 2>/dev/null', {
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe']
      }).trim();

      const pkgInfo = JSON.parse(pkgInfoRaw);
      const latestVersion = pkgInfo.version;

      console.log(`   Latest version:  ${latestVersion}`);

      if (version === latestVersion) {
        console.log('\n✅ Already up to date!\n');
        return;
      }

      console.log(`\n📦 Update available: ${version} → ${latestVersion}\n`);

      // Fetch changelog from GitHub releases
      let changelog = '';
      try {
        const https = await import('https');
        changelog = await new Promise<string>((resolve) => {
          https.get('https://api.github.com/repos/tyroneross/claude-code-debugger/releases/latest', {
            headers: { 'User-Agent': 'claude-code-debugger' }
          }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
              try {
                const release = JSON.parse(data);
                resolve(release.body || '');
              } catch {
                resolve('');
              }
            });
          }).on('error', () => resolve(''));
        });
      } catch {
        // Ignore fetch errors
      }

      // Display what's new
      console.log('📋 What\'s new:');
      if (changelog) {
        const lines = changelog.split('\n').slice(0, 5);
        lines.forEach((line: string) => {
          const trimmed = line.substring(0, 70);
          if (trimmed) console.log(`   ${trimmed}${line.length > 70 ? '...' : ''}`);
        });
      } else {
        console.log('   See: https://github.com/tyroneross/claude-code-debugger/releases');
      }
      console.log('');

      // Prompt user for confirmation unless --yes flag is passed
      let shouldInstall = options.yes;

      if (!shouldInstall) {
        const response = await prompts.default({
          type: 'confirm',
          name: 'confirm',
          message: 'Would you like to install this update?',
          initial: true
        });

        shouldInstall = response.confirm;
      }

      if (!shouldInstall) {
        console.log('\n⏭️  Update skipped.\n');
        return;
      }

      // Detect installation context
      const fs = await import('fs');
      const pathMod = await import('path');
      const cwd = process.cwd();
      const isLocalDep = fs.existsSync(pathMod.join(cwd, 'node_modules', '@tyroneross', 'claude-code-debugger'));
      const isGlobal = !isLocalDep && __dirname.includes('/lib/node_modules/');

      // Perform update via npx (always fetches latest from registry)
      console.log('\n📥 Installing update via npx...\n');

      if (isGlobal) {
        // Global install: update globally
        execSync('npm install -g @tyroneross/claude-code-debugger@latest', {
          stdio: 'inherit'
        });
      } else if (isLocalDep) {
        // Local project dependency: update in project
        execSync('npm install @tyroneross/claude-code-debugger@latest', {
          stdio: 'inherit',
          cwd
        });
      } else {
        // Running via npx: install as local dependency
        execSync('npm install @tyroneross/claude-code-debugger@latest', {
          stdio: 'inherit',
          cwd
        });
      }

      // Re-run setup to sync commands, hooks, and CLAUDE.md
      console.log('\n🔧 Syncing commands and hooks...\n');
      try {
        execSync('npx @tyroneross/claude-code-debugger@latest setup', {
          stdio: 'inherit',
          cwd,
          env: { ...process.env, INIT_CWD: cwd }
        });
      } catch {
        console.log('   ⚠ Auto-setup skipped (run manually: npx @tyroneross/claude-code-debugger setup)');
      }

      console.log('\n✅ Update complete! Now running v' + latestVersion);
      console.log('   Verify: npx @tyroneross/claude-code-debugger --version\n');
      printQuickStart();

    } catch (error: any) {
      if (error.message?.includes('npm view')) {
        console.error('❌ Could not check npm registry. Are you online?');
      } else {
        console.error('❌ Update failed:', error.message);
      }
      process.exit(1);
    }
  });

// Session context command (for hooks)
program
  .command('session-context')
  .description('Output session context as JSON (for hooks)')
  .action(async () => {
    try {
      const context = await generateSessionContext();
      console.log(JSON.stringify(context));
    } catch (error: any) {
      console.log(JSON.stringify({ ok: true, error: error.message }));
    }
  });

// Check file context command (for PreToolUse hooks)
program
  .command('check-file <filepath>')
  .description('Check if a file has past incidents (for hooks)')
  .action(async (filepath: string) => {
    try {
      const result = await checkFileContext(filepath);
      console.log(JSON.stringify(result));
    } catch (error: any) {
      console.log(JSON.stringify({ ok: true, has_incidents: false }));
    }
  });

// Detail command (drill-down into specific incident/pattern)
program
  .command('detail <id>')
  .description('Show full details for an incident or pattern')
  .action(async (id: string) => {
    try {
      if (id.startsWith('INC_')) {
        const incident = await loadIncident(id);
        if (!incident) {
          console.error(`Incident not found: ${id}`);
          process.exit(1);
        }
        console.log(JSON.stringify(incident, null, 2));
      } else if (id.startsWith('PTN_')) {
        const pattern = await loadPattern(id);
        if (!pattern) {
          console.error(`Pattern not found: ${id}`);
          process.exit(1);
        }
        console.log(JSON.stringify(pattern, null, 2));
      } else {
        console.error(`Invalid ID format: ${id} (expected INC_* or PTN_*)`);
        process.exit(1);
      }
    } catch (error: any) {
      console.error(`Error loading ${id}: ${error.message}`);
      process.exit(1);
    }
  });

// Outcome tracking command
program
  .command('outcome <incident_id> <result>')
  .description('Record whether a suggested fix worked (worked|failed|modified)')
  .option('--notes <text>', 'Additional notes about the outcome')
  .action(async (incident_id: string, result: string, options: { notes?: string }) => {
    try {
      if (!['worked', 'failed', 'modified'].includes(result)) {
        console.error('Result must be: worked, failed, or modified');
        process.exit(1);
      }

      await recordOutcome({
        incident_id,
        verdict_given: 'KNOWN_FIX', // Default — actual verdict not tracked at CLI level
        outcome: result as 'worked' | 'failed' | 'modified',
        recorded_at: Date.now(),
        notes: options.notes,
      });

      console.log(`Outcome recorded: ${incident_id} → ${result}`);
    } catch (error: any) {
      console.error(`Error recording outcome: ${error.message}`);
      process.exit(1);
    }
  });

// Rebuild keyword index command
program
  .command('rebuild-index')
  .description('Rebuild keyword index from all incidents')
  .action(async () => {
    try {
      const index = await rebuildKeywordIndex();
      console.log(`Keyword index rebuilt: ${index.total_keywords} keywords from ${index.total_incidents} incidents`);
    } catch (error: any) {
      console.error(`Error rebuilding index: ${error.message}`);
      process.exit(1);
    }
  });

// Init / onboarding command
program
  .command('init')
  .description('Interactive setup wizard for debugging memory')
  .action(async () => {
    try {
      await runInit();
    } catch (error: any) {
      console.error('Setup failed:', error.message);
      process.exit(1);
    }
  });

// Uninstall command
program
  .command('uninstall')
  .description('Remove debugging memory from this project')
  .option('--remove-data', 'Also delete all stored memory data (incidents, patterns, sessions)')
  .option('-y, --yes', 'Skip confirmation prompt')
  .action(async (options: { removeData?: boolean; yes?: boolean }) => {
    const projectRoot = process.cwd();

    console.log('\n  Claude Code Debugger — Uninstall');
    console.log('  ' + '\u2500'.repeat(40) + '\n');

    if (options.removeData) {
      console.log('  This will remove:');
      console.log('    - Session hooks from .claude/settings.json');
      console.log('    - Slash commands (/debugger, /debugger-detail, etc.)');
      console.log('    - Debugging Memory section from CLAUDE.md');
      console.log('    - ALL stored memory data (incidents, patterns, sessions)');
      console.log();
    } else {
      console.log('  This will remove:');
      console.log('    - Session hooks from .claude/settings.json');
      console.log('    - Slash commands (/debugger, /debugger-detail, etc.)');
      console.log('    - Debugging Memory section from CLAUDE.md');
      console.log();
      console.log('  Your memory data will be kept. Use --remove-data to delete it too.');
      console.log();
    }

    if (!options.yes) {
      const prompts = (await import('prompts')).default;
      const { confirm } = await prompts({
        type: 'confirm',
        name: 'confirm',
        message: options.removeData
          ? 'Remove debugger AND all memory data? This cannot be undone.'
          : 'Remove debugger from this project?',
        initial: false,
      });

      if (!confirm) {
        console.log('\n  Uninstall cancelled.\n');
        return;
      }
    }

    console.log();
    const result = await uninstall(projectRoot, { removeData: options.removeData });

    if (result.hooksRemoved) {
      console.log('  \x1b[32m+\x1b[0m Hooks removed from .claude/settings.json');
    } else {
      console.log('  \x1b[2m-\x1b[0m No hooks to remove');
    }

    if (result.commandsRemoved.length > 0) {
      console.log(`  \x1b[32m+\x1b[0m Removed ${result.commandsRemoved.length} slash commands`);
    } else {
      console.log('  \x1b[2m-\x1b[0m No commands to remove');
    }

    if (result.claudeMdCleaned) {
      console.log('  \x1b[32m+\x1b[0m Cleaned CLAUDE.md');
    } else {
      console.log('  \x1b[2m-\x1b[0m CLAUDE.md already clean');
    }

    if (options.removeData) {
      if (result.memoryRemoved) {
        console.log('  \x1b[32m+\x1b[0m Deleted .claude/memory/');
      } else {
        console.log('  \x1b[2m-\x1b[0m No memory data to remove');
      }
    }

    console.log('\n  Done. To reinstall later: claude-code-debugger init\n');

    // Suggest npm uninstall
    console.log('  \x1b[2mTo also remove the npm package:\x1b[0m');
    console.log('  \x1b[2m  npm uninstall @tyroneross/claude-code-debugger\x1b[0m\n');
  });

// ============================================================================
// Lesson commands
// ============================================================================
//
// Proactive pattern library — complements incident memory (reactive search)
// with per-repo LESSONS-LEARNED.md files meant to be read top-to-bottom during
// design/code review. See src/lessons.ts for full semantics.

const lessonCmd = program
  .command('lesson')
  .description('Manage per-repo LESSONS-LEARNED.md pattern library');

lessonCmd
  .command('add')
  .description('Append a new lesson to <repo>/LESSONS-LEARNED.md')
  .requiredOption('--repo <path>', 'target repository (absolute path or ~/)')
  .requiredOption('--title <text>', 'short descriptive title')
  .requiredOption('--category <tag>', 'short grouping tag (e.g. plugin-manifest, hooks, react-hooks)')
  .requiredOption('--pattern <text>', 'description of the generalized mistake or anti-pattern')
  .option('--date <yyyy-mm-dd>', 'date of the lesson (default: today)')
  .option('--plugins <list>', 'comma-separated list of affected plugins/packages')
  .option('--incident <id>', 'linked claude-code-debugger incident ID')
  .option('--source <url-or-path>', 'authoritative source confirming the rule')
  .option('--correct <text>', 'example of the correct approach')
  .option('--incorrect <text>', 'example of the anti-pattern')
  .option('--detect <cmd>', 'grep/audit command to detect the pattern')
  .option('--verify <text>', 'how to confirm the fix worked')
  .option('--notes <text>', 'additional context')
  .action((opts) => {
    try {
      const lesson: Lesson = {
        title: opts.title,
        date: opts.date,
        category: opts.category,
        pattern: opts.pattern,
        pluginsAffected: opts.plugins ? String(opts.plugins).split(',').map((s) => s.trim()).filter(Boolean) : undefined,
        linkedIncident: opts.incident,
        authoritativeSource: opts.source,
        correct: opts.correct,
        incorrect: opts.incorrect,
        howToDetect: opts.detect,
        verification: opts.verify,
        notes: opts.notes,
      };
      const written = addLesson(opts.repo, lesson);
      console.log(`✅ Lesson added: ${written}`);
    } catch (err: any) {
      console.error(`❌ ${err.message}`);
      process.exit(1);
    }
  });

lessonCmd
  .command('list')
  .description('List lessons in <repo>/LESSONS-LEARNED.md')
  .requiredOption('--repo <path>', 'target repository (absolute path or ~/)')
  .option('--category <tag>', 'filter by category')
  .option('--json', 'emit machine-readable JSON')
  .action((opts) => {
    try {
      const lessons = listLessons(opts.repo);
      const filtered = opts.category
        ? lessons.filter((l) => l.category === opts.category)
        : lessons;

      if (opts.json) {
        console.log(JSON.stringify(filtered, null, 2));
        return;
      }

      if (filtered.length === 0) {
        console.log('No lessons found.');
        return;
      }

      console.log(`\n📚 ${filtered.length} lesson(s) in ${opts.repo}/LESSONS-LEARNED.md\n`);
      for (const l of filtered) {
        console.log(`  ${l.date}  [${l.category || '?'}]  ${l.title}`);
      }
      console.log('');
    } catch (err: any) {
      console.error(`❌ ${err.message}`);
      process.exit(1);
    }
  });

program.parse();
