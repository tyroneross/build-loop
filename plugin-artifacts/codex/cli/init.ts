/**
 * Interactive Onboarding - `claude-code-debugger init`
 *
 * A clear, friendly setup flow:
 * 1. Welcome + what this does
 * 2. Memory mode selection
 * 3. Directory + config setup
 * 4. Self-test verification
 * 5. Quick demo
 * 6. Next steps
 */

import * as fs from 'fs';
import * as path from 'path';
import prompts from 'prompts';
import { version } from '../package.json';
import { getConfig, getMemoryPaths } from '../src/config';
import { getMemoryStats } from '../src/storage';

// ─── Terminal formatting ────────────────────────────────────────────────────

const DIM = '\x1b[2m';
const BOLD = '\x1b[1m';
const GREEN = '\x1b[32m';
const CYAN = '\x1b[36m';
const YELLOW = '\x1b[33m';
const RED = '\x1b[31m';
const RESET = '\x1b[0m';

function line(text = '') { console.log(text); }
function dim(text: string) { return `${DIM}${text}${RESET}`; }
function bold(text: string) { return `${BOLD}${text}${RESET}`; }
function green(text: string) { return `${GREEN}${text}${RESET}`; }
function cyan(text: string) { return `${CYAN}${text}${RESET}`; }
function yellow(text: string) { return `${YELLOW}${text}${RESET}`; }

function bar() { return dim('\u2500'.repeat(48)); }

function banner() {
  line();
  line(`  ${bold('Claude Code Debugger')} ${dim(`v${version}`)}`);
  line(`  ${dim('Never solve the same bug twice')}`);
  line();
  line(`  ${bar()}`);
  line();
  line(`  ${dim('What this does:')}`);
  line(`  Every time you debug something, the fix gets stored.`);
  line(`  Next time a similar bug shows up, Claude finds your`);
  line(`  past solution automatically — before you even start.`);
  line();
  line(`  ${bar()}`);
  line();
}

function stepHeader(step: number, total: number, label: string, desc?: string) {
  line(`  ${dim(`[${step}/${total}]`)} ${bold(label)}`);
  if (desc) line(`  ${dim(desc)}`);
  line();
}

// ─── Setup steps ────────────────────────────────────────────────────────────

interface SetupResult {
  mode: 'local' | 'shared';
  memoryPath: string;
  dirsCreated: boolean;
  hooksConfigured: boolean;
  commandsCreated: number;
  claudeMdUpdated: boolean;
  selfTestPassed: boolean;
}

export async function runInit(): Promise<void> {
  banner();

  const projectRoot = process.cwd();
  const projectName = path.basename(projectRoot);
  const result: SetupResult = {
    mode: 'local',
    memoryPath: '',
    dirsCreated: false,
    hooksConfigured: false,
    commandsCreated: 0,
    claudeMdUpdated: false,
    selfTestPassed: false,
  };

  // ── Check for existing setup ────────────────────────────────────────────
  const existingMemory = path.join(projectRoot, '.claude', 'memory');
  if (fs.existsSync(existingMemory)) {
    line(`  ${yellow('!')} Found existing setup in ${dim(projectName + '/.claude/memory/')}`);
    line();

    const { action } = await prompts({
      type: 'select',
      name: 'action',
      message: 'What would you like to do?',
      choices: [
        { title: 'Health check', description: 'Verify everything is working', value: 'verify' },
        { title: 'Reconfigure', description: 'Re-run setup from scratch', value: 'reconfigure' },
        { title: 'Exit', value: 'exit' },
      ],
    });

    if (action === 'exit' || action === undefined) {
      line(`\n  ${dim('Cancelled.')}\n`);
      return;
    }

    if (action === 'verify') {
      line();
      stepHeader(1, 1, 'Health Check', 'Verifying your setup is working correctly');
      await runSelfTest(result);
      printHealthReport(result);
      return;
    }
  }

  const TOTAL_STEPS = 5;

  // ── Step 1: Memory mode ─────────────────────────────────────────────────
  stepHeader(1, TOTAL_STEPS, 'Where to store memory', 'Choose where debugging incidents get saved');

  const { mode } = await prompts({
    type: 'select',
    name: 'mode',
    message: 'Storage location:',
    choices: [
      {
        title: 'This project',
        description: `.claude/memory/ — bugs specific to ${projectName}`,
        value: 'local',
      },
      {
        title: 'Shared (all projects)',
        description: '~/.claude-memory/ — learn across all your projects',
        value: 'shared',
      },
    ],
    initial: 0,
  });

  if (mode === undefined) {
    line(`\n  ${dim('Setup cancelled.')}\n`);
    return;
  }

  result.mode = mode;
  if (mode === 'shared') process.env.CLAUDE_MEMORY_MODE = 'shared';

  const config = getConfig({ storageMode: mode });
  const paths = getMemoryPaths(config);
  result.memoryPath = paths.root;

  line();

  // ── Step 2: Create directories ──────────────────────────────────────────
  stepHeader(2, TOTAL_STEPS, 'Create memory storage', 'Setting up directories for incidents, patterns, and sessions');

  try {
    fs.mkdirSync(paths.incidents, { recursive: true });
    fs.mkdirSync(paths.patterns, { recursive: true });
    fs.mkdirSync(paths.sessions, { recursive: true });
    result.dirsCreated = true;

    line(`  ${green('+')} ${dim('incidents/')}  ${dim('\u2190 where individual bug records live')}`);
    line(`  ${green('+')} ${dim('patterns/')}   ${dim('\u2190 reusable fix templates (auto-extracted)')}`);
    line(`  ${green('+')} ${dim('sessions/')}   ${dim('\u2190 temporary debugging session state')}`);
  } catch (err) {
    line(`  ${RED}x${RESET} Failed: ${(err as Error).message}`);
  }

  line();

  // ── Step 3: Configure hooks + commands ──────────────────────────────────
  stepHeader(3, TOTAL_STEPS, 'Wire up to Claude Code', 'Adding hooks and slash commands so it works automatically');

  // Hooks
  try {
    const { configureHooks } = await import('../src/setup/configure-hooks');
    const hooksOk = await configureHooks(projectRoot);
    result.hooksConfigured = !!hooksOk;
    if (hooksOk) {
      line(`  ${green('+')} Session hooks ${dim('\u2190 auto-mines bugs when sessions end')}`);
    } else {
      line(`  ${dim('-')} Hooks already configured`);
    }
  } catch {
    line(`  ${dim('-')} Hooks (skipped — will work via plugin instead)`);
  }

  // Slash commands
  try {
    const { createSlashCommands } = await import('../src/setup/create-slash-commands');
    const count = await createSlashCommands(projectRoot);
    result.commandsCreated = count;
    if (count > 0) {
      line(`  ${green('+')} ${count} slash commands ${dim('\u2190 /debugger, /debugger-detail, etc.')}`);
    } else {
      line(`  ${dim('-')} Commands already installed`);
    }
  } catch {
    line(`  ${dim('-')} Commands (skipped — will work via plugin instead)`);
  }

  // CLAUDE.md
  try {
    const { injectClaudeMd } = await import('../src/setup/inject-claude-md');
    const updated = await injectClaudeMd(projectRoot);
    result.claudeMdUpdated = !!updated;
    if (updated) {
      line(`  ${green('+')} CLAUDE.md ${dim('\u2190 tells Claude about your debugging memory')}`);
    } else {
      line(`  ${dim('-')} CLAUDE.md already has debugging section`);
    }
  } catch {
    line(`  ${dim('-')} CLAUDE.md (skipped)`);
  }

  line();

  // ── Step 4: Self-test ───────────────────────────────────────────────────
  stepHeader(4, TOTAL_STEPS, 'Verify installation', 'Running quick checks to make sure everything works');

  await runSelfTest(result);
  line();

  // ── Step 5: What's next ─────────────────────────────────────────────────
  stepHeader(5, TOTAL_STEPS, 'You\'re all set', '');

  printQuickStart(result);
}

async function runSelfTest(result: SetupResult): Promise<void> {
  const checks: { label: string; ok: boolean; detail?: string }[] = [];

  // Check 1: Memory directories exist
  const config = getConfig();
  const paths = getMemoryPaths(config);
  const dirsExist = fs.existsSync(paths.incidents) && fs.existsSync(paths.patterns);
  checks.push({
    label: 'Memory directories',
    ok: dirsExist,
    detail: dirsExist ? paths.root : 'Run: claude-code-debugger init',
  });

  // Check 2: Can write and read
  let canWrite = false;
  const testFile = path.join(paths.root, '.selftest');
  try {
    fs.writeFileSync(testFile, 'ok');
    canWrite = fs.readFileSync(testFile, 'utf-8') === 'ok';
    fs.unlinkSync(testFile);
  } catch { /* skip */ }
  checks.push({
    label: 'Read/write access',
    ok: canWrite,
    detail: canWrite ? 'Working' : 'Check directory permissions',
  });

  // Check 3: Module loads
  let moduleOk = false;
  try {
    const lib = require('../dist/src/index.js');
    moduleOk = typeof lib.checkMemory === 'function'
            && typeof lib.checkMemoryWithVerdict === 'function'
            && typeof lib.checkMemoryProgressive === 'function';
  } catch { /* skip */ }
  checks.push({
    label: 'Core module',
    ok: moduleOk,
    detail: moduleOk ? `v${version}` : 'Run: npm run build',
  });

  // Check 4: Memory stats
  let statsOk = false;
  let incidentCount = 0;
  try {
    const stats = await getMemoryStats();
    statsOk = stats !== null && stats !== undefined;
    incidentCount = stats?.total_incidents || 0;
  } catch { /* skip */ }
  checks.push({
    label: 'Memory access',
    ok: statsOk,
    detail: statsOk ? `${incidentCount} incident${incidentCount !== 1 ? 's' : ''} stored` : 'Cannot read memory',
  });

  // Check 5: Hooks configured
  const settingsPath = path.join(process.cwd(), '.claude', 'settings.json');
  let hasHooks = false;
  try {
    if (fs.existsSync(settingsPath)) {
      const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
      const stopHooks = settings?.hooks?.Stop || [];
      hasHooks = stopHooks.some((h: any) => {
        if (h.hooks) return h.hooks.some((hook: any) => hook.command?.includes('claude-code-debugger'));
        return h.command?.includes('claude-code-debugger');
      });
    }
  } catch { /* skip */ }
  checks.push({
    label: 'Auto-mining hook',
    ok: hasHooks,
    detail: hasHooks ? 'Active — mines bugs on session end' : 'Not configured (optional)',
  });

  for (const check of checks) {
    const icon = check.ok ? green('+') : yellow('!');
    const detailStr = check.detail ? dim(` ${check.detail}`) : '';
    line(`  ${icon} ${check.label}${detailStr}`);
  }

  result.selfTestPassed = checks.slice(0, 4).every(c => c.ok); // First 4 are critical
}

function printHealthReport(result: SetupResult): void {
  line();
  line(`  ${bar()}`);
  line();

  if (result.selfTestPassed) {
    line(`  ${green('Healthy.')} Everything is working.`);
  } else {
    line(`  ${yellow('Issues found.')} See checks above.`);
  }

  line();
  line(`  ${dim('Tip: Run')} claude-code-debugger status ${dim('for memory stats')}`);
  line();
}

function printQuickStart(result: SetupResult): void {
  line(`  ${bar()}`);
  line();

  if (result.selfTestPassed) {
    line(`  ${green('Ready to go.')} Here's how it works:`);
  } else {
    line(`  ${yellow('Partially set up.')} Some checks need attention (see above).`);
    line(`  The core features will still work.`);
  }

  line();
  line(`  ${bold('How debugging memory works:')}`);
  line();
  line(`  1. ${bold('You debug a bug')} — Claude fixes something as usual`);
  line(`  2. ${bold('It gets stored')}  — the session-end hook saves the fix`);
  line(`  3. ${bold('Same bug later?')} — Claude finds your past solution first`);
  line();
  line(`  ${bar()}`);
  line();
  line(`  ${bold('Commands you can use:')}`);
  line();
  line(`    ${cyan('/debugger "error message"')}  Search for similar past bugs`);
  line(`    ${cyan('/debugger-detail <ID>')}      Drill into a specific incident`);
  line(`    ${cyan('/debugger-status')}           See what's in memory`);
  line(`    ${cyan('/debugger-scan')}             Scan this session for debugging work`);
  line();
  line(`  ${bold('CLI shortcuts:')}`);
  line();
  line(`    ${cyan('claude-code-debugger status')}    Memory statistics`);
  line(`    ${cyan('claude-code-debugger search "x"')} Search from terminal`);
  line(`    ${cyan('claude-code-debugger uninstall')}  Remove (keeps your data)`);
  line();
  line(`  ${bar()}`);
  line();
  line(`  ${dim('Memory learns from every debugging session automatically.')}`);
  line(`  ${dim('The more you debug, the smarter it gets.')}`);
  line();
}
