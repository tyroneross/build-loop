/**
 * Lessons Learned — proactive pattern library.
 *
 * Complements incident memory (reactive search by symptom) with a per-repo
 * markdown file of generalizable patterns meant to be read top-to-bottom
 * during design/code review.
 *
 * Lessons are appended to `<repo>/LESSONS-LEARNED.md` with a templated block
 * bounded by `## <date> · <title>` headers. The file is plain markdown —
 * no structured database, no hidden state. A human can read and edit it
 * directly without the tool.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export interface Lesson {
  /** Short descriptive title (becomes the section header) */
  title: string;
  /** ISO date (YYYY-MM-DD). Defaults to today if omitted. */
  date?: string;
  /** Short tag for grouping (e.g. `plugin-manifest`, `hooks`, `react-hooks`, `migration`) */
  category: string;
  /** Plugins/packages affected by this lesson */
  pluginsAffected?: string[];
  /** Reference to a claude-code-debugger incident ID if this lesson was spawned from one */
  linkedIncident?: string;
  /** URL or file path to the authoritative source that confirms the rule */
  authoritativeSource?: string;
  /** The generalized mistake or anti-pattern (required) */
  pattern: string;
  /** Example of the correct approach (code snippet or description) */
  correct?: string;
  /** Example of the anti-pattern that bit you (code snippet or description) */
  incorrect?: string;
  /** Shell command or audit step to detect the pattern in existing code */
  howToDetect?: string;
  /** How to verify the fix actually worked */
  verification?: string;
  /** Any extra context worth remembering */
  notes?: string;
}

const DEFAULT_HEADER = `# Lessons Learned

Pattern-level reminders from past incidents. Use this **proactively** when designing, reviewing, or building new code.

## How this file relates to claude-code-debugger incidents

| Use | Tool |
|---|---|
| **Reactive search**: "I'm hitting error X, has this happened before?" | \`debugger search "X"\` — returns matching JSON incidents |
| **Proactive review**: "Before I ship this, what classes of bugs should I watch for?" | Read this file top to bottom |

Not redundant. Incidents are per-occurrence and searchable by symptom. Lessons are per-pattern and read in full during design. One incident can spawn a lesson; most won't. Only pattern-level generalizations belong here.

Managed by \`claude-code-debugger lesson add\` (CLI) or the \`lesson_add\` MCP tool. Hand-editable.

---
`;

/**
 * Append a lesson to `<repoPath>/LESSONS-LEARNED.md`.
 * Creates the file with a default header if it doesn't exist.
 * Returns the absolute path of the written file.
 */
export function addLesson(repoPath: string, lesson: Lesson): string {
  const absRepo = expandHome(repoPath);
  if (!fs.existsSync(absRepo) || !fs.statSync(absRepo).isDirectory()) {
    throw new Error(`Repo path does not exist or is not a directory: ${absRepo}`);
  }

  const filePath = path.join(absRepo, 'LESSONS-LEARNED.md');
  let existing = '';
  if (fs.existsSync(filePath)) {
    existing = fs.readFileSync(filePath, 'utf-8');
  } else {
    existing = DEFAULT_HEADER;
  }

  const section = renderLesson(lesson);
  const separator = existing.endsWith('\n') ? '\n' : '\n\n';
  const updated = existing + separator + section + '\n';

  // Atomic write
  const tmp = `${filePath}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, updated);
  fs.renameSync(tmp, filePath);

  return filePath;
}

/**
 * Render a Lesson as a markdown section suitable for appending to LESSONS-LEARNED.md.
 */
export function renderLesson(lesson: Lesson): string {
  const date = lesson.date ?? new Date().toISOString().slice(0, 10);
  const parts: string[] = [];

  parts.push(`## ${date} · ${lesson.title}`);
  parts.push('');

  // Metadata block
  const meta: string[] = [];
  meta.push(`**Category:** \`${lesson.category}\``);
  if (lesson.pluginsAffected?.length) {
    meta.push(`**Plugins affected:** ${lesson.pluginsAffected.join(', ')}`);
  }
  if (lesson.linkedIncident) {
    meta.push(`**Linked incident:** \`${lesson.linkedIncident}\``);
  }
  if (lesson.authoritativeSource) {
    meta.push(`**Authoritative source:** ${lesson.authoritativeSource}`);
  }
  parts.push(meta.join('  \n'));
  parts.push('');

  // Pattern (required)
  parts.push('### Pattern');
  parts.push('');
  parts.push(lesson.pattern.trim());
  parts.push('');

  if (lesson.correct) {
    parts.push('### Correct');
    parts.push('');
    parts.push(lesson.correct.trim());
    parts.push('');
  }

  if (lesson.incorrect) {
    parts.push('### Incorrect');
    parts.push('');
    parts.push(lesson.incorrect.trim());
    parts.push('');
  }

  if (lesson.howToDetect) {
    parts.push('### How to detect');
    parts.push('');
    parts.push(lesson.howToDetect.trim());
    parts.push('');
  }

  if (lesson.verification) {
    parts.push('### Verification');
    parts.push('');
    parts.push(lesson.verification.trim());
    parts.push('');
  }

  if (lesson.notes) {
    parts.push('### Notes');
    parts.push('');
    parts.push(lesson.notes.trim());
    parts.push('');
  }

  return parts.join('\n');
}

export interface LessonSummary {
  date: string;
  title: string;
  category: string;
}

/**
 * List lessons by parsing `## YYYY-MM-DD · Title` headers from the file.
 * Returns an array of summaries; use `getLesson()` to read full content by index.
 */
export function listLessons(repoPath: string): LessonSummary[] {
  const filePath = path.join(expandHome(repoPath), 'LESSONS-LEARNED.md');
  if (!fs.existsSync(filePath)) return [];

  const content = fs.readFileSync(filePath, 'utf-8');
  const lines = content.split('\n');
  const summaries: LessonSummary[] = [];

  const headerRe = /^##\s+(\d{4}-\d{2}-\d{2})\s+·\s+(.+)$/;
  const categoryRe = /^\*\*Category:\*\*\s+`([^`]+)`/;

  let currentHeader: { date: string; title: string } | null = null;
  for (const line of lines) {
    const h = line.match(headerRe);
    if (h) {
      if (currentHeader) {
        summaries.push({ ...currentHeader, category: '' });
      }
      currentHeader = { date: h[1]!, title: h[2]! };
      continue;
    }
    if (currentHeader) {
      const c = line.match(categoryRe);
      if (c) {
        summaries.push({ ...currentHeader, category: c[1]! });
        currentHeader = null;
      }
    }
  }
  if (currentHeader) {
    summaries.push({ ...currentHeader, category: '' });
  }

  return summaries;
}

function expandHome(p: string): string {
  if (p.startsWith('~/')) return path.join(os.homedir(), p.slice(2));
  if (p === '~') return os.homedir();
  return p;
}
