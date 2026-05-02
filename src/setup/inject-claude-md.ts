/**
 * Inject debugging memory documentation into CLAUDE.md
 *
 * Generates a dynamic section from index.json that includes:
 * - Current memory stats (O(1) read)
 * - Trigger instructions for when AI should call /debugger
 * - Category/file summary from index
 */

import * as fs from 'fs';
import * as path from 'path';

const STATIC_SECTION = `

## Debugging Memory

This project uses @tyroneross/claude-code-debugger for debugging memory.

**Automatic behavior:**
- Past debugging sessions are stored and indexed
- Similar incidents surface automatically when investigating bugs
- Patterns are extracted from repeated issues
- Session stop hook mines audit trail for missed incidents

**Commands:**
- \`/debugger "symptom"\` - Search past bugs for similar issues
- \`/debugger\` - Show recent bugs, pick one to debug
- \`/debugger-detail <ID>\` - Drill into a specific incident or pattern
- \`/debugger-status\` - Show memory statistics
- \`/debugger-scan\` - Scan recent sessions for debugging work

The system learns from your debugging sessions automatically.
`;

const MARKER = '## Debugging Memory';
const DYNAMIC_MARKER_START = '<!-- debugger-dynamic-start -->';
const DYNAMIC_MARKER_END = '<!-- debugger-dynamic-end -->';

/**
 * Try to read index.json and generate a dynamic section
 */
function generateDynamicSectionSync(projectRoot: string): string {
  const indexPath = path.join(projectRoot, '.claude', 'memory', 'index.json');

  try {
    if (!fs.existsSync(indexPath)) return '';

    const raw = fs.readFileSync(indexPath, 'utf-8');
    const index = JSON.parse(raw);

    if (!index.stats || index.stats.total_incidents === 0) return '';

    const topCats = Object.entries(index.stats.categories || {})
      .sort(([, a]: any, [, b]: any) => b - a)
      .slice(0, 5)
      .map(([cat, count]) => `${cat}(${count})`)
      .join(', ');

    const hotFiles = Object.entries(index.by_file || {})
      .sort(([, a]: any, [, b]: any) => b.length - a.length)
      .slice(0, 5)
      .map(([file, ids]: any) => `\`${file}\`(${ids.length})`)
      .join(', ');

    const qd = index.stats.quality_distribution || {};

    return `
${DYNAMIC_MARKER_START}
## Debugging Memory (Auto-Active)

**Trigger:** Call \`/debugger "symptom"\` BEFORE investigating when you see:
- Error messages, stack traces, test failures
- "fix", "debug", "broken", "not working" from user
- Editing a file listed in Hot Files below

**Memory State:** ${index.stats.total_incidents} incidents | ${index.stats.total_patterns} patterns | Quality: ${qd.excellent || 0}E/${qd.good || 0}G/${qd.fair || 0}F
**Categories:** ${topCats || 'none'}
**Hot files:** ${hotFiles || 'none'}
**Progressive:** Use \`/debugger-detail <ID>\` to drill into any match
${DYNAMIC_MARKER_END}
`;
  } catch {
    return '';
  }
}

export async function injectClaudeMd(projectRoot: string): Promise<boolean> {
  const claudeMdPath = path.join(projectRoot, 'CLAUDE.md');

  let content = '';
  if (fs.existsSync(claudeMdPath)) {
    content = fs.readFileSync(claudeMdPath, 'utf-8');

    // Update dynamic section if it exists
    if (content.includes(DYNAMIC_MARKER_START)) {
      const dynamicSection = generateDynamicSectionSync(projectRoot);
      if (dynamicSection) {
        const regex = new RegExp(
          `${DYNAMIC_MARKER_START}[\\s\\S]*?${DYNAMIC_MARKER_END}`,
          'g'
        );
        content = content.replace(regex, dynamicSection.trim());
        fs.writeFileSync(claudeMdPath, content);
        return true;
      }
      return false;
    }

    // Don't duplicate static section
    if (content.includes(MARKER)) {
      // Append dynamic section after existing static section
      const dynamicSection = generateDynamicSectionSync(projectRoot);
      if (dynamicSection) {
        content += dynamicSection;
        fs.writeFileSync(claudeMdPath, content);
        return true;
      }
      return false;
    }
  }

  // First time — add static + dynamic
  const dynamicSection = generateDynamicSectionSync(projectRoot);
  content += STATIC_SECTION + dynamicSection;
  fs.writeFileSync(claudeMdPath, content);
  return true;
}
