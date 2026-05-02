/**
 * Configure Claude Code hooks for automatic debugging memory integration
 */

import * as fs from 'fs';
import * as path from 'path';

export async function configureHooks(projectRoot: string): Promise<boolean> {
  const settingsPath = path.join(projectRoot, '.claude', 'settings.json');
  const claudeDir = path.dirname(settingsPath);

  if (!fs.existsSync(claudeDir)) {
    fs.mkdirSync(claudeDir, { recursive: true });
  }

  let settings: Record<string, unknown> = {};
  if (fs.existsSync(settingsPath)) {
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
    } catch {
      settings = {};
    }
  }

  // Initialize hooks object if needed
  if (!settings.hooks || typeof settings.hooks !== 'object') {
    settings.hooks = {};
  }

  const hooks = settings.hooks as Record<string, unknown[]>;

  // Hook: On session stop, auto-mine recent audit trail
  // New format uses matcher + hooks array structure
  type HookEntry = {
    matcher?: string;  // Empty string "" matches all events
    hooks?: Array<{ type?: string; command?: string }>;
    // Legacy format fields
    type?: string;
    command?: string;
  };

  const stopHooks = (hooks.Stop || []) as HookEntry[];

  // Check for existing debugger hook in both old and new formats
  const hasMemoryHook = stopHooks.some((h) => {
    // Check new format
    if (h.hooks) {
      return h.hooks.some(hook => hook.command?.includes('claude-code-debugger'));
    }
    // Check legacy format
    return h.command?.includes('claude-code-debugger');
  });

  if (!hasMemoryHook) {
    // Use new matcher-based format
    stopHooks.push({
      matcher: "",
      hooks: [
        {
          type: 'command',
          command: 'npx @tyroneross/claude-code-debugger mine --days 1 --store 2>/dev/null || true'
        }
      ]
    });
    hooks.Stop = stopHooks;
    settings.hooks = hooks;

    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
    return true;
  }

  return false;
}
