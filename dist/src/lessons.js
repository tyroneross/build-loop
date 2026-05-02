"use strict";
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
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.addLesson = addLesson;
exports.renderLesson = renderLesson;
exports.listLessons = listLessons;
const node_fs_1 = __importDefault(require("node:fs"));
const node_path_1 = __importDefault(require("node:path"));
const node_os_1 = __importDefault(require("node:os"));
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
function addLesson(repoPath, lesson) {
    const absRepo = expandHome(repoPath);
    if (!node_fs_1.default.existsSync(absRepo) || !node_fs_1.default.statSync(absRepo).isDirectory()) {
        throw new Error(`Repo path does not exist or is not a directory: ${absRepo}`);
    }
    const filePath = node_path_1.default.join(absRepo, 'LESSONS-LEARNED.md');
    let existing = '';
    if (node_fs_1.default.existsSync(filePath)) {
        existing = node_fs_1.default.readFileSync(filePath, 'utf-8');
    }
    else {
        existing = DEFAULT_HEADER;
    }
    const section = renderLesson(lesson);
    const separator = existing.endsWith('\n') ? '\n' : '\n\n';
    const updated = existing + separator + section + '\n';
    // Atomic write
    const tmp = `${filePath}.tmp-${process.pid}`;
    node_fs_1.default.writeFileSync(tmp, updated);
    node_fs_1.default.renameSync(tmp, filePath);
    return filePath;
}
/**
 * Render a Lesson as a markdown section suitable for appending to LESSONS-LEARNED.md.
 */
function renderLesson(lesson) {
    const date = lesson.date ?? new Date().toISOString().slice(0, 10);
    const parts = [];
    parts.push(`## ${date} · ${lesson.title}`);
    parts.push('');
    // Metadata block
    const meta = [];
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
/**
 * List lessons by parsing `## YYYY-MM-DD · Title` headers from the file.
 * Returns an array of summaries; use `getLesson()` to read full content by index.
 */
function listLessons(repoPath) {
    const filePath = node_path_1.default.join(expandHome(repoPath), 'LESSONS-LEARNED.md');
    if (!node_fs_1.default.existsSync(filePath))
        return [];
    const content = node_fs_1.default.readFileSync(filePath, 'utf-8');
    const lines = content.split('\n');
    const summaries = [];
    const headerRe = /^##\s+(\d{4}-\d{2}-\d{2})\s+·\s+(.+)$/;
    const categoryRe = /^\*\*Category:\*\*\s+`([^`]+)`/;
    let currentHeader = null;
    for (const line of lines) {
        const h = line.match(headerRe);
        if (h) {
            if (currentHeader) {
                summaries.push({ ...currentHeader, category: '' });
            }
            currentHeader = { date: h[1], title: h[2] };
            continue;
        }
        if (currentHeader) {
            const c = line.match(categoryRe);
            if (c) {
                summaries.push({ ...currentHeader, category: c[1] });
                currentHeader = null;
            }
        }
    }
    if (currentHeader) {
        summaries.push({ ...currentHeader, category: '' });
    }
    return summaries;
}
function expandHome(p) {
    if (p.startsWith('~/'))
        return node_path_1.default.join(node_os_1.default.homedir(), p.slice(2));
    if (p === '~')
        return node_os_1.default.homedir();
    return p;
}
//# sourceMappingURL=lessons.js.map