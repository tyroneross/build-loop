/**
 * Debugging Memory System - Storage Layer
 *
 * Handles reading/writing incidents and patterns to filesystem.
 * File-based storage for simplicity and portability.
 * Now supports configurable paths (local or shared mode).
 */

import fs from 'fs/promises';
import path from 'path';
import type { Incident, Pattern, StorageOptions, VerificationResult, MemoryConfig, MemoryIndex, IncidentLogEntry, KeywordIndex, VerdictOutcome } from './types';
import { getMemoryPaths } from './config';
import { buildIncidentInteractive, calculateQualityScore } from './interactive-verifier';
import { traced } from './logger';

/**
 * Store an incident in memory
 */
export async function storeIncident(
  incident: Incident,
  options: StorageOptions & { config?: MemoryConfig; interactive?: boolean } = {}
): Promise<{ incident_id: string; file_path: string }> {
  return traced('storage:storeIncident', { id: incident.incident_id }, async () => {

  let finalIncident = incident;

  // Use interactive mode if requested
  if (options.interactive) {
    finalIncident = await buildIncidentInteractive(incident);
  }

  // Calculate quality score if not already set
  if (!finalIncident.completeness?.quality_score) {
    const qualityScore = calculateQualityScore(finalIncident);

    if (!finalIncident.completeness) {
      finalIncident.completeness = {
        symptom: !!finalIncident.symptom && finalIncident.symptom.length >= 20,
        root_cause: !!finalIncident.root_cause?.description && finalIncident.root_cause.description.length >= 50,
        fix: !!finalIncident.fix?.approach && (finalIncident.fix.changes?.length || 0) > 0,
        verification: finalIncident.verification?.status === 'verified',
        quality_score: qualityScore
      };
    } else {
      finalIncident.completeness.quality_score = qualityScore;
    }
  }

  // Validate if requested
  if (options.validate_schema) {
    const validation = validateIncident(finalIncident);
    if (!validation.valid) {
      throw new Error(`Invalid incident: ${validation.errors.join(', ')}`);
    }
  }

  // Get paths from config
  const paths = getMemoryPaths(options.config);
  const INCIDENTS_DIR = paths.incidents;

  // Ensure directory exists
  await fs.mkdir(INCIDENTS_DIR, { recursive: true });

  // Generate filename
  const filename = `${finalIncident.incident_id}.json`;
  const filepath = path.join(INCIDENTS_DIR, filename);

  // Write to file
  await fs.writeFile(
    filepath,
    JSON.stringify(finalIncident, null, 2),
    'utf-8'
  );

  const qualityEmoji = (finalIncident.completeness.quality_score >= 0.75) ? '🌟' :
                       (finalIncident.completeness.quality_score >= 0.5) ? '✅' : '⚠️';

  console.log(`${qualityEmoji} Incident stored: ${finalIncident.incident_id} (quality: ${(finalIncident.completeness.quality_score * 100).toFixed(0)}%)`);

  // Append to JSONL log for fast full-text search
  await appendToIncidentLog(finalIncident, options.config);

  // Incremental index update (avoids loading all incidents)
  await updateIndexIncremental(finalIncident, options.config);

  // Update keyword index
  await updateKeywordIndex(finalIncident, options.config);

  return {
    incident_id: finalIncident.incident_id,
    file_path: filepath
  };
  });
}

/**
 * Validate incident ID format to prevent path traversal attacks
 * Supports both formats: INC_YYYYMMDD_HHMMSS_xxxx and INC_CATEGORY_YYYYMMDD_HHMMSS_xxxx
 */
function isValidIncidentId(incident_id: string): boolean {
  return /^INC_[\w\-]+$/.test(incident_id);
}

/**
 * Load an incident by ID
 */
export async function loadIncident(incident_id: string, config?: MemoryConfig): Promise<Incident | null> {
  return traced('storage:loadIncident', { id: incident_id }, async () => {
  // Validate incident ID to prevent path traversal
  if (!isValidIncidentId(incident_id)) {
    throw new Error(`Invalid incident ID format: ${incident_id}`);
  }

  const paths = getMemoryPaths(config);
  const filename = `${incident_id}.json`;
  const filepath = path.join(paths.incidents, filename);

  try {
    const content = await fs.readFile(filepath, 'utf-8');
    return JSON.parse(content) as Incident;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return null; // File not found
    }
    throw error;
  }
  });
}

/**
 * Load all incidents with batched I/O (max 50 concurrent reads)
 */
export async function loadAllIncidents(config?: MemoryConfig): Promise<Incident[]> {
  const paths = getMemoryPaths(config);
  const INCIDENTS_DIR = paths.incidents;

  try {
    const files = await fs.readdir(INCIDENTS_DIR);
    const jsonFiles = files.filter(f => f.endsWith('.json'));
    const incidents: Incident[] = [];

    const BATCH_SIZE = 50;
    for (let i = 0; i < jsonFiles.length; i += BATCH_SIZE) {
      const batch = jsonFiles.slice(i, i + BATCH_SIZE);
      const results = await Promise.all(
        batch.map(async (file) => {
          try {
            const filepath = path.join(INCIDENTS_DIR, file);
            const content = await fs.readFile(filepath, 'utf-8');
            return JSON.parse(content) as Incident;
          } catch {
            return null;
          }
        })
      );
      incidents.push(...results.filter((r): r is Incident => r !== null));
    }

    return incidents;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return []; // Directory doesn't exist yet
    }
    throw error;
  }
}

/**
 * Store a pattern
 */
export async function storePattern(pattern: Pattern, config?: MemoryConfig): Promise<string> {
  const paths = getMemoryPaths(config);
  await fs.mkdir(paths.patterns, { recursive: true });

  const filename = `${pattern.pattern_id}.json`;
  const filepath = path.join(paths.patterns, filename);

  await fs.writeFile(
    filepath,
    JSON.stringify(pattern, null, 2),
    'utf-8'
  );

  console.log(`✅ Pattern stored: ${pattern.pattern_id}`);

  return filepath;
}

/**
 * Validate pattern ID format to prevent path traversal attacks
 */
function isValidPatternId(pattern_id: string): boolean {
  // Only allow alphanumeric and underscores (PTN_CATEGORY_NAME format)
  return /^PTN_[\w]+$/.test(pattern_id);
}

/**
 * Load a pattern by ID
 */
export async function loadPattern(pattern_id: string, config?: MemoryConfig): Promise<Pattern | null> {
  // Validate pattern ID to prevent path traversal
  if (!isValidPatternId(pattern_id)) {
    throw new Error(`Invalid pattern ID format: ${pattern_id}`);
  }

  const paths = getMemoryPaths(config);
  const filename = `${pattern_id}.json`;
  const filepath = path.join(paths.patterns, filename);

  try {
    const content = await fs.readFile(filepath, 'utf-8');
    return JSON.parse(content) as Pattern;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return null;
    }
    throw error;
  }
}

/**
 * Load all patterns with batched I/O (max 50 concurrent reads)
 */
export async function loadAllPatterns(config?: MemoryConfig): Promise<Pattern[]> {
  const paths = getMemoryPaths(config);
  const PATTERNS_DIR = paths.patterns;

  try {
    const files = await fs.readdir(PATTERNS_DIR);
    const jsonFiles = files.filter(f => f.endsWith('.json'));
    const patterns: Pattern[] = [];

    const BATCH_SIZE = 50;
    for (let i = 0; i < jsonFiles.length; i += BATCH_SIZE) {
      const batch = jsonFiles.slice(i, i + BATCH_SIZE);
      const results = await Promise.all(
        batch.map(async (file) => {
          try {
            const filepath = path.join(PATTERNS_DIR, file);
            const content = await fs.readFile(filepath, 'utf-8');
            return JSON.parse(content) as Pattern;
          } catch {
            return null;
          }
        })
      );
      patterns.push(...results.filter((r): r is Pattern => r !== null));
    }

    return patterns;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return [];
    }
    throw error;
  }
}

/**
 * Validate incident structure
 */
export function validateIncident(incident: Incident): VerificationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  // Required fields
  if (!incident.incident_id) errors.push('Missing incident_id');
  if (!incident.timestamp) errors.push('Missing timestamp');
  if (!incident.symptom) errors.push('Missing symptom');
  if (!incident.root_cause) errors.push('Missing root_cause');
  if (!incident.fix) errors.push('Missing fix');

  // Check completeness
  if (incident.root_cause && !incident.root_cause.confidence) {
    warnings.push('Root cause missing confidence score');
  }

  if (incident.verification && incident.verification.status === 'unverified') {
    warnings.push('Incident not verified');
  }

  if (!incident.quality_gates || !incident.quality_gates.guardian_validated) {
    warnings.push('Not validated by guardian (expected in current system)');
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings
  };
}

/**
 * Generate incident ID with optional category prefix for self-documenting filenames
 *
 * Without category: INC_20250215_143022_a1b2
 * With category:    INC_API_20250215_143022_a1b2
 */
export function generateIncidentId(category?: string): string {
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
  const timeStr = now.toISOString().slice(11, 19).replace(/:/g, '');
  const random = Math.random().toString(36).substring(2, 6);

  if (category) {
    const cleanCat = category.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 12);
    return `INC_${cleanCat}_${dateStr}_${timeStr}_${random}`;
  }

  return `INC_${dateStr}_${timeStr}_${random}`;
}

/**
 * Generate pattern ID
 */
export function generatePatternId(category: string, name: string): string {
  const cleanCategory = category.toUpperCase().replace(/[^A-Z]/g, '_');
  const cleanName = name.toUpperCase().replace(/[^A-Z]/g, '_');

  return `PTN_${cleanCategory}_${cleanName}`;
}

/**
 * Get memory statistics
 */
export async function getMemoryStats(config?: MemoryConfig): Promise<{
  total_incidents: number;
  total_patterns: number;
  oldest_incident: number;
  newest_incident: number;
  disk_usage_kb: number;
}> {
  const incidents = await loadAllIncidents(config);
  const patterns = await loadAllPatterns(config);

  const timestamps = incidents.map(i => i.timestamp).filter(Boolean);
  const oldest = timestamps.length > 0 ? Math.min(...timestamps) : 0;
  const newest = timestamps.length > 0 ? Math.max(...timestamps) : 0;

  // Estimate disk usage (rough)
  const diskUsage =
    incidents.length * 1 + // ~1KB per incident
    patterns.length * 2;    // ~2KB per pattern

  return {
    total_incidents: incidents.length,
    total_patterns: patterns.length,
    oldest_incident: oldest,
    newest_incident: newest,
    disk_usage_kb: diskUsage
  };
}

// ============================================================================
// TOKEN OPTIMIZATION - Compact Serializers
// ============================================================================

import type {
  CompactIncident,
  CompactPattern,
  IncidentSummary,
} from './types';

/**
 * Truncate text to max length with ellipsis
 */
function truncate(text: string | undefined, maxLen: number): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.substring(0, maxLen - 3) + '...';
}

/**
 * Generate minimal incident summary (~100 tokens)
 */
export function generateIncidentSummary(incident: Incident): IncidentSummary {
  return {
    incident_id: incident.incident_id,
    symptom_preview: truncate(incident.symptom, 80),
    root_cause_preview: truncate(incident.root_cause?.description, 100),
    fix_preview: truncate(incident.fix?.approach, 80),
    category: incident.root_cause?.category || 'unknown',
    confidence: incident.root_cause?.confidence || 0,
    quality: incident.completeness?.quality_score || 0,
  };
}

/**
 * Convert full incident to compact format (~200 tokens)
 *
 * Uses short keys to minimize token usage in LLM context.
 * Preserves essential information for debugging assistance.
 */
export function toCompactIncident(incident: Incident): CompactIncident {
  return {
    id: incident.incident_id,
    ts: incident.timestamp,
    sym: truncate(incident.symptom, 80),
    rc: {
      d: truncate(incident.root_cause?.description, 100),
      cat: incident.root_cause?.category || 'unknown',
      conf: incident.root_cause?.confidence || 0,
    },
    fix: {
      a: truncate(incident.fix?.approach, 80),
      n: incident.fix?.changes?.length || 0,
    },
    v: incident.verification?.status === 'verified'
      ? 'V'
      : incident.verification?.status === 'partial'
      ? 'P'
      : 'U',
    t: (incident.tags || []).slice(0, 5),
    q: incident.completeness?.quality_score || 0,
    sim: incident.similarity_score,
  };
}

/**
 * Convert full pattern to compact format (~120 tokens)
 */
export function toCompactPattern(pattern: Pattern): CompactPattern {
  return {
    id: pattern.pattern_id,
    n: pattern.usage_history?.total_uses || 1,
    desc: truncate(pattern.description, 100),
    sig: pattern.detection_signature.slice(0, 5),
    fix: truncate(pattern.solution_template, 150),
    sr: pattern.success_rate,
    cat: pattern.tags[0] || 'general',
    t: pattern.tags.slice(0, 5),
    last: pattern.last_used || Date.now(),
  };
}

/**
 * Batch convert incidents to compact format
 */
export function toCompactIncidents(incidents: Incident[]): CompactIncident[] {
  return incidents.map(toCompactIncident);
}

/**
 * Batch convert patterns to compact format
 */
export function toCompactPatterns(patterns: Pattern[]): CompactPattern[] {
  return patterns.map(toCompactPattern);
}

/**
 * Estimate tokens for a compact incident
 */
export function estimateCompactIncidentTokens(incident: CompactIncident): number {
  // Rough estimate: JSON structure + field names + values
  const json = JSON.stringify(incident);
  return Math.ceil(json.length / 4);
}

/**
 * Estimate tokens for a compact pattern
 */
export function estimateCompactPatternTokens(pattern: CompactPattern): number {
  const json = JSON.stringify(pattern);
  return Math.ceil(json.length / 4);
}

/**
 * Enforce token budget on results
 *
 * Returns limited incidents and patterns that fit within budget.
 * Patterns get 30% budget, incidents get 60%, metadata gets 10%.
 */
export function enforceTokenBudget(
  incidents: CompactIncident[],
  patterns: CompactPattern[],
  budget: number = 2500
): {
  limitedIncidents: CompactIncident[];
  limitedPatterns: CompactPattern[];
  tokensUsed: number;
  truncated: { incidents: number; patterns: number };
} {
  const patternBudget = Math.floor(budget * 0.3);
  const incidentBudget = Math.floor(budget * 0.6);

  // Estimate tokens per item (using averages from DEFAULT_TOKEN_BUDGET)
  const tokensPerPattern = 120;
  const tokensPerIncident = 200;

  const maxPatterns = Math.floor(patternBudget / tokensPerPattern);
  const maxIncidents = Math.floor(incidentBudget / tokensPerIncident);

  const limitedPatterns = patterns.slice(0, maxPatterns);
  const limitedIncidents = incidents.slice(0, maxIncidents);

  const tokensUsed =
    limitedPatterns.length * tokensPerPattern +
    limitedIncidents.length * tokensPerIncident +
    Math.floor(budget * 0.1); // metadata overhead

  return {
    limitedPatterns,
    limitedIncidents,
    tokensUsed,
    truncated: {
      incidents: incidents.length - limitedIncidents.length,
      patterns: patterns.length - limitedPatterns.length,
    },
  };
}

/**
 * Load incidents and return compact versions
 */
export async function loadCompactIncidents(
  config?: MemoryConfig
): Promise<CompactIncident[]> {
  const incidents = await loadAllIncidents(config);
  return toCompactIncidents(incidents);
}

/**
 * Load patterns and return compact versions
 */
export async function loadCompactPatterns(
  config?: MemoryConfig
): Promise<CompactPattern[]> {
  const patterns = await loadAllPatterns(config);
  return toCompactPatterns(patterns);
}

// ============================================================================
// JSONL APPEND LOG - Fast full-text search without loading individual files
// ============================================================================

/**
 * Append incident entry to JSONL log for fast search
 */
export async function appendToIncidentLog(
  incident: Incident,
  config?: MemoryConfig
): Promise<void> {
  const paths = getMemoryPaths(config);
  const logPath = path.join(paths.root, 'incidents.jsonl');

  const entry: IncidentLogEntry = {
    incident_id: incident.incident_id,
    timestamp: incident.timestamp,
    symptom: incident.symptom,
    category: incident.root_cause?.category || 'unknown',
    tags: incident.tags || [],
    quality_score: incident.completeness?.quality_score || 0,
    verification_status: incident.verification?.status || 'unverified',
    files_changed: incident.files_changed || [],
  };

  try {
    await fs.appendFile(logPath, JSON.stringify(entry) + '\n', 'utf-8');
  } catch {
    // Log file doesn't exist yet, create it
    await fs.mkdir(paths.root, { recursive: true });
    await fs.writeFile(logPath, JSON.stringify(entry) + '\n', 'utf-8');
  }
}

/**
 * Search JSONL log for fast text matching (avoids loading all incident files)
 */
export async function searchIncidentLog(
  query: string,
  config?: MemoryConfig
): Promise<IncidentLogEntry[]> {
  const paths = getMemoryPaths(config);
  const logPath = path.join(paths.root, 'incidents.jsonl');

  try {
    const content = await fs.readFile(logPath, 'utf-8');
    const queryLower = query.toLowerCase();

    return content
      .split('\n')
      .filter(line => line.trim())
      .map(line => {
        try { return JSON.parse(line) as IncidentLogEntry; }
        catch { return null; }
      })
      .filter((entry): entry is IncidentLogEntry => {
        if (!entry) return false;
        return (
          entry.incident_id.toLowerCase().includes(queryLower) ||
          entry.symptom.toLowerCase().includes(queryLower) ||
          entry.category.toLowerCase().includes(queryLower) ||
          entry.tags.some(t => t.toLowerCase().includes(queryLower))
        );
      });
  } catch {
    return [];
  }
}

// ============================================================================
// MEMORY INDEX - O(1) lookups by category, tag, file
// ============================================================================

/**
 * Build or rebuild the memory index from all incidents
 */
export async function rebuildIndex(config?: MemoryConfig): Promise<MemoryIndex> {
  return traced('storage:rebuildIndex', undefined, async () => {
  const incidents = await loadAllIncidents(config);
  const paths = getMemoryPaths(config);

  const index: MemoryIndex = {
    version: 1,
    last_updated: Date.now(),
    stats: {
      total_incidents: incidents.length,
      total_patterns: 0,
      categories: {},
      tags: {},
      quality_distribution: { excellent: 0, good: 0, fair: 0 },
      oldest_timestamp: Infinity,
      newest_timestamp: 0,
    },
    by_category: {},
    by_tag: {},
    by_file: {},
    by_quality: { excellent: [], good: [], fair: [] },
    recent: [],
  };

  // Count patterns
  try {
    const patternFiles = await fs.readdir(paths.patterns);
    index.stats.total_patterns = patternFiles.filter(f => f.endsWith('.json')).length;
  } catch { /* patterns dir may not exist */ }

  // Build indexes
  for (const incident of incidents) {
    const id = incident.incident_id;
    const cat = incident.root_cause?.category || 'unknown';
    const qs = incident.completeness?.quality_score || 0;

    // Category index
    if (!index.by_category[cat]) index.by_category[cat] = [];
    index.by_category[cat].push(id);
    index.stats.categories[cat] = (index.stats.categories[cat] || 0) + 1;

    // Tag index
    for (const tag of (incident.tags || [])) {
      if (!index.by_tag[tag]) index.by_tag[tag] = [];
      index.by_tag[tag].push(id);
      index.stats.tags[tag] = (index.stats.tags[tag] || 0) + 1;
    }

    // File index
    for (const file of (incident.files_changed || [])) {
      if (!index.by_file[file]) index.by_file[file] = [];
      index.by_file[file].push(id);
    }

    // Quality distribution
    if (qs >= 0.75) {
      index.by_quality.excellent.push(id);
      index.stats.quality_distribution.excellent++;
    } else if (qs >= 0.5) {
      index.by_quality.good.push(id);
      index.stats.quality_distribution.good++;
    } else {
      index.by_quality.fair.push(id);
      index.stats.quality_distribution.fair++;
    }

    // Timestamps
    if (incident.timestamp < index.stats.oldest_timestamp) {
      index.stats.oldest_timestamp = incident.timestamp;
    }
    if (incident.timestamp > index.stats.newest_timestamp) {
      index.stats.newest_timestamp = incident.timestamp;
    }
  }

  // Fix infinity if no incidents
  if (index.stats.oldest_timestamp === Infinity) index.stats.oldest_timestamp = 0;

  // Recent (newest first, max 20)
  index.recent = incidents
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, 20)
    .map(i => i.incident_id);

  // Write index
  const indexPath = path.join(paths.root, 'index.json');
  await fs.mkdir(paths.root, { recursive: true });
  await fs.writeFile(indexPath, JSON.stringify(index, null, 2), 'utf-8');

  return index;
  });
}

/**
 * Load memory index (fast, no incident file reads needed)
 */
export async function loadIndex(config?: MemoryConfig): Promise<MemoryIndex | null> {
  const paths = getMemoryPaths(config);
  const indexPath = path.join(paths.root, 'index.json');

  try {
    const content = await fs.readFile(indexPath, 'utf-8');
    return JSON.parse(content) as MemoryIndex;
  } catch {
    return null;
  }
}

// ============================================================================
// MEMORY SUMMARY - Compressed context for LLM cold starts
// ============================================================================

/**
 * Build a compact MEMORY_SUMMARY.md for LLM context injection
 * Stays under 150 lines to fit in context windows
 */
export async function buildMemorySummary(config?: MemoryConfig): Promise<string> {
  const index = await loadIndex(config) || await rebuildIndex(config);
  const patterns = await loadAllPatterns(config);

  const lines: string[] = [];

  lines.push('# Debugging Memory Summary');
  lines.push(`> ${index.stats.total_incidents} incidents | ${index.stats.total_patterns} patterns`);
  lines.push(`> Quality: ${index.stats.quality_distribution.excellent} excellent, ${index.stats.quality_distribution.good} good, ${index.stats.quality_distribution.fair} fair`);
  lines.push('');

  // Top categories
  const sortedCats = Object.entries(index.stats.categories)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 10);

  if (sortedCats.length > 0) {
    lines.push('## Categories');
    for (const [cat, count] of sortedCats) {
      lines.push(`- **${cat}**: ${count} incidents`);
    }
    lines.push('');
  }

  // Top tags
  const sortedTags = Object.entries(index.stats.tags)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 15);

  if (sortedTags.length > 0) {
    lines.push('## Common Tags');
    lines.push(sortedTags.map(([tag, count]) => `\`${tag}\`(${count})`).join(', '));
    lines.push('');
  }

  // Patterns
  if (patterns.length > 0) {
    lines.push('## Known Patterns');
    for (const p of patterns.slice(0, 10)) {
      lines.push(`- **${p.name}** (${(p.success_rate * 100).toFixed(0)}% success) - ${p.description.slice(0, 80)}`);
    }
    lines.push('');
  }

  // Recent incidents (from index, no file reads)
  if (index.recent.length > 0) {
    lines.push('## Recent Incident IDs');
    lines.push(index.recent.slice(0, 10).map(id => `\`${id}\``).join(', '));
    lines.push('');
  }

  // Hot files
  const sortedFiles = Object.entries(index.by_file)
    .sort(([, a], [, b]) => b.length - a.length)
    .slice(0, 10);

  if (sortedFiles.length > 0) {
    lines.push('## Frequently Affected Files');
    for (const [file, ids] of sortedFiles) {
      lines.push(`- \`${file}\` (${ids.length} incidents)`);
    }
    lines.push('');
  }

  lines.push(`*Updated: ${new Date().toISOString().slice(0, 19)}*`);

  const content = lines.join('\n');

  // Write to disk
  const paths = getMemoryPaths(config);
  const summaryPath = path.join(paths.root, 'MEMORY_SUMMARY.md');
  await fs.mkdir(paths.root, { recursive: true });
  await fs.writeFile(summaryPath, content, 'utf-8');

  return content;
}

// ============================================================================
// MEMORY ARCHIVAL - Evict old incidents to keep active set manageable
// ============================================================================

import type { ArchiveManifest } from './types';

/**
 * Archive incidents older than maxAge days, keeping at most maxActive incidents
 */
export async function archiveOldIncidents(
  options: { maxActive?: number; maxAgeDays?: number; dryRun?: boolean } = {},
  config?: MemoryConfig
): Promise<{ archived: string[]; kept: number }> {
  const { maxActive = 200, maxAgeDays = 180, dryRun = false } = options;
  const paths = getMemoryPaths(config);
  const archivePath = path.join(paths.root, 'archive');

  const incidents = await loadAllIncidents(config);
  const sorted = incidents.sort((a, b) => b.timestamp - a.timestamp);

  const cutoff = Date.now() - maxAgeDays * 24 * 60 * 60 * 1000;
  const toArchive: Incident[] = [];

  for (let i = 0; i < sorted.length; i++) {
    if (i >= maxActive || sorted[i].timestamp < cutoff) {
      toArchive.push(sorted[i]);
    }
  }

  if (toArchive.length === 0) {
    return { archived: [], kept: incidents.length };
  }

  console.log(`📦 ${dryRun ? '[DRY RUN] Would archive' : 'Archiving'} ${toArchive.length} incidents`);

  if (!dryRun) {
    // Create archive directory with timestamp
    const archiveDir = path.join(archivePath, `archive_${Date.now()}`);
    await fs.mkdir(archiveDir, { recursive: true });

    // Move incidents to archive
    for (const incident of toArchive) {
      const srcPath = path.join(paths.incidents, `${incident.incident_id}.json`);
      const destPath = path.join(archiveDir, `${incident.incident_id}.json`);
      try {
        await fs.rename(srcPath, destPath);
      } catch {
        // File may already be archived or missing
      }
    }

    // Write manifest
    const manifest: ArchiveManifest = {
      archived_at: Date.now(),
      incident_count: toArchive.length,
      oldest_timestamp: Math.min(...toArchive.map(i => i.timestamp)),
      newest_timestamp: Math.max(...toArchive.map(i => i.timestamp)),
      reason: `Exceeded ${maxActive} active or older than ${maxAgeDays} days`,
    };
    await fs.writeFile(
      path.join(archiveDir, 'manifest.json'),
      JSON.stringify(manifest, null, 2),
      'utf-8'
    );

    // Rebuild index and summary
    await rebuildIndex(config);
    await buildMemorySummary(config);
  }

  return {
    archived: toArchive.map(i => i.incident_id),
    kept: incidents.length - toArchive.length,
  };
}

// ============================================================================
// CONTEXT COMPRESSION - Compress memory for LLM context injection
// ============================================================================

/**
 * Generate compressed context string optimized for LLM consumption
 * Fits within token budget while maximizing information density
 */
export function compressContext(
  incidents: CompactIncident[],
  patterns: CompactPattern[],
  budget: number = 2500
): string {
  const { limitedIncidents, limitedPatterns, tokensUsed, truncated } =
    enforceTokenBudget(incidents, patterns, budget);

  const sections: string[] = [];

  // Header
  sections.push(`[Memory: ${limitedIncidents.length}i/${limitedPatterns.length}p, ~${tokensUsed}tok]`);

  // Patterns first (highest value)
  if (limitedPatterns.length > 0) {
    sections.push('PATTERNS:');
    for (const p of limitedPatterns) {
      sections.push(`  ${p.id}|${p.cat}|${(p.sr * 100).toFixed(0)}%|${p.desc}`);
      sections.push(`  fix: ${p.fix}`);
    }
  }

  // Incidents
  if (limitedIncidents.length > 0) {
    sections.push('INCIDENTS:');
    for (const inc of limitedIncidents) {
      const vLabel = inc.v === 'V' ? 'verified' : inc.v === 'P' ? 'partial' : 'unverified';
      sections.push(`  ${inc.id}|${inc.rc.cat}|${(inc.rc.conf * 100).toFixed(0)}%|${vLabel}`);
      sections.push(`  sym: ${inc.sym}`);
      sections.push(`  fix: ${inc.fix.a}`);
    }
  }

  // Truncation notice
  if (truncated.incidents > 0 || truncated.patterns > 0) {
    sections.push(`[+${truncated.incidents}i/${truncated.patterns}p omitted]`);
  }

  return sections.join('\n');
}

// ============================================================================
// KEYWORD INDEX - Inverted index for scalable retrieval
// ============================================================================

/**
 * Extract keywords from an incident for indexing
 */
function extractIndexKeywords(incident: Incident): string[] {
  const stopWords = new Set([
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
    'can', 'could', 'may', 'might', 'must', 'this', 'that', 'these', 'those',
    'not', 'from', 'it', 'its', 'when', 'where', 'what', 'which', 'how',
  ]);

  const text = [
    incident.symptom || '',
    incident.root_cause?.description || '',
    incident.root_cause?.category || '',
    incident.fix?.approach || '',
    ...(incident.tags || []),
  ].join(' ');

  return [...new Set(
    text
      .toLowerCase()
      .replace(/[^\w\s]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 2 && !stopWords.has(w))
  )];
}

/**
 * Update keyword index with a new incident (incremental, no full rebuild)
 */
export async function updateKeywordIndex(
  incident: Incident,
  config?: MemoryConfig
): Promise<void> {
  const index = await loadKeywordIndex(config) || {
    version: 1,
    last_updated: Date.now(),
    keywords: {},
    total_incidents: 0,
    total_keywords: 0,
  };

  const keywords = extractIndexKeywords(incident);
  const id = incident.incident_id;

  for (const kw of keywords) {
    if (!index.keywords[kw]) index.keywords[kw] = [];
    if (!index.keywords[kw].includes(id)) {
      index.keywords[kw].push(id);
    }
  }

  index.last_updated = Date.now();
  index.total_incidents++;
  index.total_keywords = Object.keys(index.keywords).length;

  const paths = getMemoryPaths(config);
  const indexPath = path.join(paths.root, 'keyword-index.json');
  await fs.mkdir(paths.root, { recursive: true });
  await fs.writeFile(indexPath, JSON.stringify(index), 'utf-8');
}

/**
 * Load keyword index from disk
 */
export async function loadKeywordIndex(config?: MemoryConfig): Promise<KeywordIndex | null> {
  const paths = getMemoryPaths(config);
  const indexPath = path.join(paths.root, 'keyword-index.json');

  try {
    const content = await fs.readFile(indexPath, 'utf-8');
    return JSON.parse(content) as KeywordIndex;
  } catch {
    return null;
  }
}

/**
 * Find candidate incident IDs by keyword intersection
 */
export function findCandidatesByKeyword(
  queryWords: string[],
  index: KeywordIndex,
  maxCandidates: number = 20
): string[] {
  const candidateScores = new Map<string, number>();

  for (const word of queryWords) {
    const ids = index.keywords[word] || [];
    for (const id of ids) {
      candidateScores.set(id, (candidateScores.get(id) || 0) + 1);
    }
  }

  // Sort by number of keyword hits (descending)
  return [...candidateScores.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, maxCandidates)
    .map(([id]) => id);
}

/**
 * Rebuild keyword index from scratch
 */
export async function rebuildKeywordIndex(config?: MemoryConfig): Promise<KeywordIndex> {
  const incidents = await loadAllIncidents(config);

  const index: KeywordIndex = {
    version: 1,
    last_updated: Date.now(),
    keywords: {},
    total_incidents: incidents.length,
    total_keywords: 0,
  };

  for (const incident of incidents) {
    const keywords = extractIndexKeywords(incident);
    const id = incident.incident_id;

    for (const kw of keywords) {
      if (!index.keywords[kw]) index.keywords[kw] = [];
      if (!index.keywords[kw].includes(id)) {
        index.keywords[kw].push(id);
      }
    }
  }

  index.total_keywords = Object.keys(index.keywords).length;

  const paths = getMemoryPaths(config);
  const indexPath = path.join(paths.root, 'keyword-index.json');
  await fs.mkdir(paths.root, { recursive: true });
  await fs.writeFile(indexPath, JSON.stringify(index), 'utf-8');

  return index;
}

// ============================================================================
// INCREMENTAL INDEX UPDATE - Avoid full rebuilds on every store
// ============================================================================

/**
 * Update index incrementally with a new incident (avoids full loadAllIncidents)
 */
export async function updateIndexIncremental(
  incident: Incident,
  config?: MemoryConfig
): Promise<void> {
  const paths = getMemoryPaths(config);
  const indexPath = path.join(paths.root, 'index.json');

  let index: MemoryIndex;
  try {
    const content = await fs.readFile(indexPath, 'utf-8');
    index = JSON.parse(content) as MemoryIndex;
  } catch {
    // No index yet — do a full rebuild
    await rebuildIndex(config);
    return;
  }

  const id = incident.incident_id;
  const cat = incident.root_cause?.category || 'unknown';
  const qs = incident.completeness?.quality_score || 0;

  // Stats
  index.stats.total_incidents++;
  index.stats.categories[cat] = (index.stats.categories[cat] || 0) + 1;
  for (const tag of (incident.tags || [])) {
    index.stats.tags[tag] = (index.stats.tags[tag] || 0) + 1;
  }

  if (incident.timestamp < index.stats.oldest_timestamp || index.stats.oldest_timestamp === 0) {
    index.stats.oldest_timestamp = incident.timestamp;
  }
  if (incident.timestamp > index.stats.newest_timestamp) {
    index.stats.newest_timestamp = incident.timestamp;
  }

  // Category index
  if (!index.by_category[cat]) index.by_category[cat] = [];
  index.by_category[cat].push(id);

  // Tag index
  for (const tag of (incident.tags || [])) {
    if (!index.by_tag[tag]) index.by_tag[tag] = [];
    index.by_tag[tag].push(id);
  }

  // File index
  for (const file of (incident.files_changed || [])) {
    if (!index.by_file[file]) index.by_file[file] = [];
    index.by_file[file].push(id);
  }

  // Quality
  if (qs >= 0.75) {
    index.by_quality.excellent.push(id);
    index.stats.quality_distribution.excellent++;
  } else if (qs >= 0.5) {
    index.by_quality.good.push(id);
    index.stats.quality_distribution.good++;
  } else {
    index.by_quality.fair.push(id);
    index.stats.quality_distribution.fair++;
  }

  // Recent
  index.recent.unshift(id);
  if (index.recent.length > 20) index.recent = index.recent.slice(0, 20);

  index.last_updated = Date.now();

  await fs.writeFile(indexPath, JSON.stringify(index, null, 2), 'utf-8');
}

// ============================================================================
// OUTCOME TRACKING - Did the suggested fix actually work?
// ============================================================================

/**
 * Record whether a verdict suggestion actually worked
 */
export async function recordOutcome(
  outcome: VerdictOutcome,
  config?: MemoryConfig
): Promise<void> {
  const paths = getMemoryPaths(config);
  const outcomesPath = path.join(paths.root, 'outcomes.jsonl');

  try {
    await fs.appendFile(outcomesPath, JSON.stringify(outcome) + '\n', 'utf-8');
  } catch {
    await fs.mkdir(paths.root, { recursive: true });
    await fs.writeFile(outcomesPath, JSON.stringify(outcome) + '\n', 'utf-8');
  }
}

/**
 * Load all recorded outcomes
 */
export async function loadOutcomes(config?: MemoryConfig): Promise<VerdictOutcome[]> {
  const paths = getMemoryPaths(config);
  const outcomesPath = path.join(paths.root, 'outcomes.jsonl');

  try {
    const content = await fs.readFile(outcomesPath, 'utf-8');
    return content
      .split('\n')
      .filter(line => line.trim())
      .map(line => {
        try { return JSON.parse(line) as VerdictOutcome; }
        catch { return null; }
      })
      .filter((o): o is VerdictOutcome => o !== null);
  } catch {
    return [];
  }
}

/**
 * Get outcome statistics for a specific incident
 */
export async function getOutcomeStats(
  incident_id: string,
  config?: MemoryConfig
): Promise<{ worked: number; failed: number; modified: number }> {
  const outcomes = await loadOutcomes(config);
  const relevant = outcomes.filter(o => o.incident_id === incident_id);

  return {
    worked: relevant.filter(o => o.outcome === 'worked').length,
    failed: relevant.filter(o => o.outcome === 'failed').length,
    modified: relevant.filter(o => o.outcome === 'modified').length,
  };
}
