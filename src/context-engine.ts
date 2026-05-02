/**
 * Context Engine - Auto-injection, file-aware context, and session context
 *
 * Three layers of progressive context:
 * 1. CLAUDE.md dynamic section (~250 tokens, always present)
 * 2. Session-start context (compact JSON with stats + triggers)
 * 3. File-aware pre-tool context (incident IDs for files being edited)
 */

import type { MemoryConfig, MemoryIndex } from './types';
import { loadIndex } from './storage';
import { getMemoryPaths } from './config';

/**
 * Generate the dynamic CLAUDE.md section from index data
 *
 * Reads index.json (O(1), no file scan) and produces a ~250 token section
 * that tells the AI WHEN to invoke /debugger.
 */
export async function generateDynamicSection(config?: MemoryConfig): Promise<string> {
  const index = await loadIndex(config);

  if (!index || index.stats.total_incidents === 0) {
    return `
## Debugging Memory (Auto-Active)

**Trigger:** Call \`/debugger "symptom"\` BEFORE investigating when you see:
- Error messages, stack traces, test failures
- "fix", "debug", "broken", "not working" from user
- Editing a file that previously had bugs

**Memory State:** Empty — no incidents stored yet
**Start building memory** by documenting fixes with /debugger-scan
`;
  }

  // Build category summary
  const topCategories = Object.entries(index.stats.categories)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5)
    .map(([cat, count]) => `${cat}(${count})`)
    .join(', ');

  // Build hot files list
  const hotFiles = Object.entries(index.by_file)
    .sort(([, a], [, b]) => b.length - a.length)
    .slice(0, 5)
    .map(([file, ids]) => `${file}(${ids.length})`)
    .join(', ');

  return `
## Debugging Memory (Auto-Active)

**Trigger:** Call \`/debugger "symptom"\` BEFORE investigating when you see:
- Error messages, stack traces, test failures
- "fix", "debug", "broken", "not working" from user
- Editing a file listed in Hot Files below

**Memory State:** ${index.stats.total_incidents} incidents | ${index.stats.total_patterns} patterns | Quality: ${index.stats.quality_distribution.excellent}E/${index.stats.quality_distribution.good}G/${index.stats.quality_distribution.fair}F
**Categories:** ${topCategories || 'none'}
**Hot files:** ${hotFiles || 'none'}
**Progressive:** Use \`/debugger-detail <ID>\` to drill into any match
`;
}

/**
 * Generate session-start context — compact JSON for the SessionStart hook
 *
 * Output: ~150 tokens of structured context.
 */
export async function generateSessionContext(config?: MemoryConfig): Promise<{
  ok: boolean;
  memory_state: {
    incidents: number;
    patterns: number;
    categories: string[];
    hot_files: string[];
    recent_ids: string[];
  };
  triggers: string[];
}> {
  const index = await loadIndex(config);

  if (!index || index.stats.total_incidents === 0) {
    return {
      ok: true,
      memory_state: {
        incidents: 0,
        patterns: 0,
        categories: [],
        hot_files: [],
        recent_ids: [],
      },
      triggers: [
        'Call /debugger "symptom" when you see errors or test failures',
        'Document fixes with /debugger-scan at session end',
      ],
    };
  }

  const topCategories = Object.entries(index.stats.categories)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5)
    .map(([cat]) => cat);

  const hotFiles = Object.entries(index.by_file)
    .sort(([, a], [, b]) => b.length - a.length)
    .slice(0, 5)
    .map(([file]) => file);

  return {
    ok: true,
    memory_state: {
      incidents: index.stats.total_incidents,
      patterns: index.stats.total_patterns,
      categories: topCategories,
      hot_files: hotFiles,
      recent_ids: index.recent.slice(0, 5),
    },
    triggers: [
      'Call /debugger "symptom" BEFORE investigating errors',
      `Hot files with past bugs: ${hotFiles.slice(0, 3).join(', ') || 'none'}`,
      'Use /debugger-detail <ID> to drill into matches',
    ],
  };
}

/**
 * Check if a file has past incidents — for PreToolUse file-aware hook
 *
 * Returns incident IDs if file has past bugs, or {ok: true} if clean.
 * Zero noise for files without history.
 */
export async function checkFileContext(
  filepath: string,
  config?: MemoryConfig
): Promise<{
  ok: boolean;
  has_incidents: boolean;
  incident_ids?: string[];
  message?: string;
}> {
  const index = await loadIndex(config);

  if (!index) {
    return { ok: true, has_incidents: false };
  }

  // Normalize filepath for lookup
  const normalizedPath = filepath.replace(/^\.\//, '');
  const ids = index.by_file[normalizedPath] || index.by_file[filepath] || [];

  if (ids.length === 0) {
    return { ok: true, has_incidents: false };
  }

  return {
    ok: true,
    has_incidents: true,
    incident_ids: ids.slice(0, 5),
    message: `This file has ${ids.length} past incident(s). Run /debugger-detail ${ids[0]} to review before editing.`,
  };
}
