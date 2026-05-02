/**
 * Debugger MCP Tool Definitions
 *
 * 8 tools for debugging memory: search, store, detail, status, list, patterns, outcome, read_logs.
 * Each calls existing programmatic APIs from storage.ts and retrieval.ts.
 * Responses are formatted as concise text for LLM consumption.
 */

import {
  storeIncident,
  generateIncidentId,
  loadIncident,
  loadPattern,
  loadIndex,
  loadAllPatterns,
  toCompactPattern,
  recordOutcome,
} from '../storage';
import { checkMemoryWithVerdict } from '../retrieval';
import { readProjectLogs } from '../log-reader';
import type { Incident, VerdictOutcome } from '../types';
import type { LogSource, LogSeverity } from '../log-reader';
import { traced } from '../logger';

// --- Console safety ---

/**
 * Redirect console.log to stderr for the duration of an async call.
 * storeIncident() uses console.log with emoji output — in a stdio MCP server,
 * any console.log corrupts the JSON-RPC stream (stdout = protocol channel).
 */
function withSilentConsole<T>(fn: () => Promise<T>): Promise<T> {
  const origLog = console.log;
  console.log = (...args: unknown[]) =>
    process.stderr.write(args.map(String).join(' ') + '\n');
  return fn().finally(() => {
    console.log = origLog;
  });
}

// --- Response helpers ---

function textResponse(text: string) {
  return { content: [{ type: 'text' as const, text }] };
}

function errorResponse(text: string) {
  return { content: [{ type: 'text' as const, text }], isError: true as const };
}

// --- Tool definitions ---

export const TOOLS = [
  {
    name: 'search',
    description:
      'Check debugging memory for similar past issues. Returns a verdict (KNOWN_FIX, LIKELY_MATCH, WEAK_SIGNAL, NO_MATCH) with matching incidents and patterns. Use before investigating any bug.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        symptom: {
          type: 'string',
          description: 'Description of the bug or error symptom to search for',
        },
      },
      required: ['symptom'],
    },
    annotations: {
      title: 'Search Debugging Memory',
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: 'store',
    description:
      'Store a new debugging incident after fixing a bug. Provide the symptom, root cause, fix details, tags, and files changed.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        symptom: {
          type: 'string',
          description: 'User-facing description of the bug',
        },
        root_cause: {
          type: 'string',
          description: 'Technical explanation of why the bug occurred',
        },
        category: {
          type: 'string',
          description:
            'Root cause category (e.g. logic, config, dependency, performance, react-hooks)',
        },
        fix: {
          type: 'string',
          description: 'What was done to fix the bug',
        },
        tags: {
          type: 'array',
          items: { type: 'string' },
          description: 'Search keywords for future retrieval',
        },
        files_changed: {
          type: 'array',
          items: { type: 'string' },
          description: 'List of files that were modified',
        },
        file: {
          type: 'string',
          description: 'Primary file where the bug was located',
        },
      },
      required: ['symptom', 'root_cause', 'fix'],
    },
    annotations: {
      title: 'Store Debugging Incident',
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: false,
    },
  },
  {
    name: 'detail',
    description:
      'Get full details of a specific incident (INC_*) or pattern (PTN_*). Use after search returns compact results and you need the full data.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        id: {
          type: 'string',
          description: 'Incident ID (INC_*) or pattern ID (PTN_*)',
        },
      },
      required: ['id'],
    },
    annotations: {
      title: 'Get Incident/Pattern Details',
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: 'status',
    description:
      'Debugging memory statistics — total incidents, patterns, categories, quality distribution, and hot files.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
    annotations: {
      title: 'Memory Status',
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: 'list',
    description:
      'List recent incidents, optionally filtered by category. Returns compact summaries.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        limit: {
          type: 'number',
          description: 'Maximum number of incidents to return (default: 10)',
        },
        category: {
          type: 'string',
          description: 'Filter by root cause category (e.g. logic, config, dependency)',
        },
      },
    },
    annotations: {
      title: 'List Recent Incidents',
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: 'patterns',
    description:
      'List known fix patterns with success rates. Patterns are reusable solutions extracted from 3+ similar incidents.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        limit: {
          type: 'number',
          description: 'Maximum number of patterns to return (default: 10)',
        },
      },
    },
    annotations: {
      title: 'List Fix Patterns',
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: 'outcome',
    description:
      'Record whether a suggested fix worked, failed, or was modified. Improves future verdict accuracy.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        incident_id: {
          type: 'string',
          description: 'The incident ID that was suggested (INC_*)',
        },
        result: {
          type: 'string',
          enum: ['worked', 'failed', 'modified'],
          description: 'Whether the suggested fix worked, failed, or was modified',
        },
        notes: {
          type: 'string',
          description: 'Optional notes about the outcome',
        },
      },
      required: ['incident_id', 'result'],
    },
    annotations: {
      title: 'Record Fix Outcome',
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: 'read_logs',
    description:
      'Read and search log files from the debugger, current project, or a specific path. Auto-discovers common log locations. Supports time range, severity, and keyword filtering.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        source: {
          type: 'string',
          enum: ['debugger', 'project', 'path'],
          description:
            'Log source: "debugger" for internal operation logs, "project" for auto-discovered project logs, "path" for a specific file',
        },
        path: {
          type: 'string',
          description: 'File path (required when source is "path")',
        },
        since: {
          type: 'string',
          description:
            'Start time filter — ISO 8601 (2024-01-15T10:00:00Z) or relative (1h, 30m, 7d)',
        },
        until: {
          type: 'string',
          description: 'End time filter — same format as since',
        },
        level: {
          type: 'string',
          enum: ['debug', 'info', 'warn', 'error', 'fatal'],
          description: 'Minimum severity level to include',
        },
        keyword: {
          type: 'string',
          description: 'Search keyword to filter log messages',
        },
        limit: {
          type: 'number',
          description: 'Maximum entries to return (default: 50)',
        },
      },
      required: ['source'],
    },
    annotations: {
      title: 'Read Logs',
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
];

// --- Tool handlers ---

export async function handleToolCall(
  name: string,
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }>; isError?: boolean }> {
  // Wrap all handlers — storage.ts and retrieval.ts both use console.log
  // which would corrupt the JSON-RPC stdout stream
  return withSilentConsole(async () => {
    try {
      switch (name) {
        case 'search':
          return await traced('mcp:search', args, () => handleSearch(args));
        case 'store':
          return await traced('mcp:store', args, () => handleStore(args));
        case 'detail':
          return await traced('mcp:detail', args, () => handleDetail(args));
        case 'status':
          return await traced('mcp:status', undefined, () => handleStatus());
        case 'list':
          return await traced('mcp:list', args, () => handleList(args));
        case 'patterns':
          return await traced('mcp:patterns', args, () => handlePatterns(args));
        case 'outcome':
          return await traced('mcp:outcome', args, () => handleOutcome(args));
        case 'read_logs':
          return await traced('mcp:read_logs', args, () => handleReadLogs(args));
        default:
          return errorResponse(`Unknown tool: ${name}`);
      }
    } catch (err) {
      return errorResponse(
        err instanceof Error ? err.message : 'Tool execution failed'
      );
    }
  });
}

// --- Individual handlers ---

async function handleSearch(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }> }> {
  const symptom = String(args.symptom || '');
  if (!symptom.trim()) {
    return errorResponse('symptom is required');
  }

  const result = await checkMemoryWithVerdict(symptom);

  const lines = [
    `Verdict: ${result.verdict}`,
    `${result.summary}`,
    `Confidence: ${(result.confidence * 100).toFixed(0)}%`,
    `Action: ${result.action}`,
  ];

  if (result.patterns.length > 0) {
    lines.push(`\nPatterns (${result.patterns.length}):`);
    for (const p of result.patterns) {
      lines.push(
        `- ${p.id}: ${p.desc} (${((p.sr ?? 0) * 100).toFixed(0)}% success, ${p.n} uses)`
      );
    }
  }

  if (result.incidents.length > 0) {
    lines.push(`\nIncidents (${result.incidents.length}):`);
    for (const inc of result.incidents) {
      lines.push(`- ${inc.id}: ${inc.sym}`);
      if (inc.rc) {
        lines.push(`  Root cause: ${inc.rc.d} (${inc.rc.cat})`);
      }
    }
  }

  lines.push(`\nTokens used: ${result.tokens_used}`);

  return textResponse(lines.join('\n'));
}

async function handleStore(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }> }> {
  const symptom = String(args.symptom || '');
  const rootCause = String(args.root_cause || '');
  const fixApproach = String(args.fix || '');

  if (!symptom.trim()) return errorResponse('symptom is required');
  if (!rootCause.trim()) return errorResponse('root_cause is required');
  if (!fixApproach.trim()) return errorResponse('fix is required');

  const category = String(args.category || 'general');
  const tags = Array.isArray(args.tags)
    ? args.tags.map(String)
    : [];
  const filesChanged = Array.isArray(args.files_changed)
    ? args.files_changed.map(String)
    : [];
  const file = args.file ? String(args.file) : undefined;

  const incidentId = generateIncidentId(category);

  const incident: Incident = {
    incident_id: incidentId,
    timestamp: Date.now(),
    symptom,
    root_cause: {
      description: rootCause,
      file,
      category,
      confidence: 0.8,
    },
    fix: {
      approach: fixApproach,
      changes: filesChanged.map((f) => ({
        file: f,
        lines_changed: 0,
        change_type: 'modify' as const,
        summary: '',
      })),
    },
    verification: {
      status: 'unverified',
      regression_tests_passed: false,
      user_journey_tested: false,
      success_criteria_met: false,
    },
    tags,
    files_changed: filesChanged,
    quality_gates: {
      guardian_validated: false,
      tested_e2e: false,
      tested_from_ui: false,
      security_reviewed: false,
      architect_reviewed: false,
    },
    completeness: {
      symptom: symptom.length >= 20,
      root_cause: rootCause.length >= 50,
      fix: !!fixApproach,
      verification: false,
      quality_score: 0,
    },
  };

  const result = await storeIncident(incident);

  return textResponse(
    `Incident stored: ${result.incident_id}\nFile: ${result.file_path}`
  );
}

async function handleDetail(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }>; isError?: boolean }> {
  const id = String(args.id || '');
  if (!id.trim()) return errorResponse('id is required');

  if (id.startsWith('PTN_')) {
    const pattern = await loadPattern(id);
    if (!pattern) return errorResponse(`Pattern not found: ${id}`);

    const lines = [
      `Pattern: ${pattern.pattern_id}`,
      `Name: ${pattern.name}`,
      `Description: ${pattern.description}`,
      `Success rate: ${(pattern.success_rate * 100).toFixed(0)}%`,
      `Uses: ${pattern.usage_history?.total_uses || 0}`,
      `Tags: ${pattern.tags.join(', ')}`,
      `\nDetection signatures: ${pattern.detection_signature.join(', ')}`,
      `\nSolution:\n${pattern.solution_template}`,
    ];

    if (pattern.caveats?.length) {
      lines.push(`\nCaveats:\n${pattern.caveats.map((c) => `- ${c}`).join('\n')}`);
    }

    return textResponse(lines.join('\n'));
  }

  if (id.startsWith('INC_')) {
    const incident = await loadIncident(id);
    if (!incident) return errorResponse(`Incident not found: ${id}`);

    const lines = [
      `Incident: ${incident.incident_id}`,
      `Date: ${new Date(incident.timestamp).toISOString()}`,
      `Symptom: ${incident.symptom}`,
      `\nRoot cause: ${incident.root_cause.description}`,
      `Category: ${incident.root_cause.category}`,
      `Confidence: ${(incident.root_cause.confidence * 100).toFixed(0)}%`,
    ];

    if (incident.root_cause.file) {
      lines.push(`File: ${incident.root_cause.file}`);
    }

    lines.push(`\nFix: ${incident.fix.approach}`);

    if (incident.fix.changes?.length) {
      lines.push('Changes:');
      for (const c of incident.fix.changes) {
        lines.push(`- ${c.file}: ${c.summary || c.change_type} (${c.lines_changed} lines)`);
      }
    }

    lines.push(`\nVerification: ${incident.verification.status}`);
    lines.push(`Tags: ${incident.tags.join(', ')}`);
    lines.push(`Files changed: ${incident.files_changed.join(', ')}`);

    const quality = incident.completeness?.quality_score ?? incident.quality_score ?? 0;
    lines.push(`Quality: ${(quality * 100).toFixed(0)}%`);

    return textResponse(lines.join('\n'));
  }

  return errorResponse(`Invalid ID format. Expected INC_* or PTN_*, got: ${id}`);
}

async function handleStatus(): Promise<{
  content: Array<{ type: string; text: string }>;
}> {
  const index = await loadIndex();

  if (!index) {
    return textResponse(
      'No debugging memory found. Store incidents after fixing bugs to build memory.'
    );
  }

  const lines = [
    `Debugging Memory Status`,
    `Total incidents: ${index.stats.total_incidents}`,
    `Total patterns: ${index.stats.total_patterns}`,
  ];

  // Categories
  const cats = Object.entries(index.stats.categories)
    .sort(([, a], [, b]) => b - a);
  if (cats.length > 0) {
    lines.push(`\nCategories:`);
    for (const [cat, count] of cats) {
      lines.push(`- ${cat}: ${count}`);
    }
  }

  // Quality distribution
  const q = index.stats.quality_distribution;
  lines.push(`\nQuality: ${q.excellent} excellent, ${q.good} good, ${q.fair} fair`);

  // Top tags
  const tags = Object.entries(index.stats.tags)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 10);
  if (tags.length > 0) {
    lines.push(`\nTop tags: ${tags.map(([t, c]) => `${t}(${c})`).join(', ')}`);
  }

  // Hot files
  const files = Object.entries(index.by_file)
    .map(([f, ids]) => [f, ids.length] as [string, number])
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5);
  if (files.length > 0) {
    lines.push(`\nHot files (most incidents):`);
    for (const [f, count] of files) {
      lines.push(`- ${f}: ${count} incidents`);
    }
  }

  // Timespan
  if (index.stats.oldest_timestamp && index.stats.newest_timestamp) {
    const oldest = new Date(index.stats.oldest_timestamp).toISOString().slice(0, 10);
    const newest = new Date(index.stats.newest_timestamp).toISOString().slice(0, 10);
    lines.push(`\nTimespan: ${oldest} to ${newest}`);
  }

  return textResponse(lines.join('\n'));
}

async function handleList(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }> }> {
  const limit = typeof args.limit === 'number' ? args.limit : 10;
  const category = args.category ? String(args.category) : undefined;

  const index = await loadIndex();
  if (!index) {
    return textResponse('No debugging memory found.');
  }

  let incidentIds: string[];

  if (category) {
    incidentIds = index.by_category[category] || [];
    if (incidentIds.length === 0) {
      const available = Object.keys(index.by_category).join(', ');
      return textResponse(
        `No incidents in category "${category}". Available: ${available}`
      );
    }
    // Most recent first (IDs contain timestamps)
    incidentIds = incidentIds.slice().reverse().slice(0, limit);
  } else {
    incidentIds = index.recent.slice(0, limit);
  }

  if (incidentIds.length === 0) {
    return textResponse('No incidents found.');
  }

  const lines = [
    category
      ? `Recent incidents (${category}, showing ${incidentIds.length}):`
      : `Recent incidents (showing ${incidentIds.length}):`,
  ];

  for (const id of incidentIds) {
    const incident = await loadIncident(id);
    if (!incident) {
      lines.push(`- ${id}: (file missing)`);
      continue;
    }
    const date = new Date(incident.timestamp).toISOString().slice(0, 10);
    const cat = incident.root_cause?.category || 'unknown';
    const sym = incident.symptom.substring(0, 60);
    lines.push(`- ${id} [${date}] ${cat}: ${sym}`);
  }

  return textResponse(lines.join('\n'));
}

async function handlePatterns(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }> }> {
  const limit = typeof args.limit === 'number' ? args.limit : 10;

  const patterns = await loadAllPatterns();
  if (patterns.length === 0) {
    return textResponse(
      'No patterns found. Patterns are extracted when 3+ similar incidents exist.'
    );
  }

  const sorted = patterns
    .sort((a, b) => b.success_rate - a.success_rate)
    .slice(0, limit);

  const lines = [`Fix patterns (${sorted.length} of ${patterns.length}):`];

  for (const p of sorted) {
    const compact = toCompactPattern(p);
    lines.push(
      `- ${compact.id}: ${compact.desc} (${((compact.sr ?? 0) * 100).toFixed(0)}% success, ${compact.n} uses)`
    );
    if (p.detection_signature.length > 0) {
      lines.push(`  Signatures: ${p.detection_signature.slice(0, 3).join(', ')}`);
    }
  }

  return textResponse(lines.join('\n'));
}

async function handleOutcome(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }>; isError?: boolean }> {
  const incidentId = String(args.incident_id || '');
  const result = String(args.result || '');
  const notes = args.notes ? String(args.notes) : undefined;

  if (!incidentId.trim()) return errorResponse('incident_id is required');
  if (!['worked', 'failed', 'modified'].includes(result)) {
    return errorResponse('result must be one of: worked, failed, modified');
  }

  const outcome: VerdictOutcome = {
    incident_id: incidentId,
    verdict_given: 'LIKELY_MATCH', // Default — the exact verdict isn't always known at outcome time
    outcome: result as 'worked' | 'failed' | 'modified',
    recorded_at: Date.now(),
    notes,
  };

  await recordOutcome(outcome);

  return textResponse(`Outcome recorded for ${incidentId}: ${result}`);
}

async function handleReadLogs(
  args: Record<string, unknown>
): Promise<{ content: Array<{ type: string; text: string }>; isError?: boolean }> {
  const source = String(args.source || '') as LogSource;
  if (!['debugger', 'project', 'path'].includes(source)) {
    return errorResponse('source must be one of: debugger, project, path');
  }

  if (source === 'path' && !args.path) {
    return errorResponse('path is required when source is "path"');
  }

  const result = await readProjectLogs({
    source,
    path: args.path ? String(args.path) : undefined,
    since: args.since ? String(args.since) : undefined,
    until: args.until ? String(args.until) : undefined,
    level: args.level ? String(args.level) as LogSeverity : undefined,
    keyword: args.keyword ? String(args.keyword) : undefined,
    limit: typeof args.limit === 'number' ? args.limit : undefined,
  });

  if (result.entries.length === 0) {
    const filesInfo = result.files_read.length > 0
      ? `\nFiles checked: ${result.files_read.join(', ')}`
      : '\nNo log files found.';
    return textResponse(`No log entries found matching filters.${filesInfo}`);
  }

  const lines = [
    `Log entries (${result.entries.length}${result.truncated ? ` of ${result.total_matched}` : ''}):`,
    `Files: ${result.files_read.join(', ')}`,
    '',
  ];

  for (const entry of result.entries) {
    const ts = entry.timestamp.slice(0, 19).replace('T', ' ');
    const lvl = entry.level.toUpperCase().padEnd(5);
    lines.push(`[${ts}] ${lvl} ${entry.message}`);
  }

  if (result.truncated) {
    lines.push(`\n... ${result.total_matched - result.entries.length} more entries (use limit to see more)`);
  }

  return textResponse(lines.join('\n'));
}
