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
/**
 * Append a lesson to `<repoPath>/LESSONS-LEARNED.md`.
 * Creates the file with a default header if it doesn't exist.
 * Returns the absolute path of the written file.
 */
export declare function addLesson(repoPath: string, lesson: Lesson): string;
/**
 * Render a Lesson as a markdown section suitable for appending to LESSONS-LEARNED.md.
 */
export declare function renderLesson(lesson: Lesson): string;
export interface LessonSummary {
    date: string;
    title: string;
    category: string;
}
/**
 * List lessons by parsing `## YYYY-MM-DD · Title` headers from the file.
 * Returns an array of summaries; use `getLesson()` to read full content by index.
 */
export declare function listLessons(repoPath: string): LessonSummary[];
//# sourceMappingURL=lessons.d.ts.map