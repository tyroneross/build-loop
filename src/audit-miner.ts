/**
 * Audit Trail Miner
 *
 * Recovers debugging incidents from .claude/audit files when agents fail to store them manually.
 * This is the 85% fallback layer when wrapper functions aren't used or agents drop the ball.
 */

import fs from 'fs/promises';
import path from 'path';
import type { Incident, MemoryConfig } from './types';
import { storeIncident, generateIncidentId, loadAllIncidents } from './storage';
import { getConfig } from './config';
import { traced } from './logger';

const getAuditDir = () => path.join(process.cwd(), '.claude/audit');

interface AuditFinding {
  file: string;
  symptom: string;
  root_cause?: string;
  fix?: string;
  confidence: number;
  timestamp: number;
}

/**
 * Mine audit trail for incidents
 */
export async function mineAuditTrail(options: {
  days_back?: number;
  auto_store?: boolean;
  min_confidence?: number;
  config?: MemoryConfig;
} = {}): Promise<Incident[]> {
  return traced('audit:mineTrail', { days_back: options.days_back }, async () => {

  const {
    days_back = 30,
    auto_store = false,
    min_confidence = 0.7,
    config
  } = options;

  console.log('⛏️  Mining Audit Trail for Incidents\n');
  console.log(`   Looking back: ${days_back} days`);
  console.log(`   Min confidence: ${(min_confidence * 100).toFixed(0)}%\n`);

  // Step 1: Find all audit markdown files
  const auditFiles = await findAuditFiles(days_back);
  console.log(`📁 Found ${auditFiles.length} audit files\n`);

  // Step 2: Extract incidents from files
  const findings: AuditFinding[] = [];

  for (const file of auditFiles) {
    const extracted = await extractIncidentsFromFile(file);
    findings.push(...extracted);
  }

  console.log(`🔍 Extracted ${findings.length} potential incidents\n`);

  // Step 3: Filter by confidence
  const filtered = findings.filter(f => f.confidence >= min_confidence);
  console.log(`✅ ${filtered.length} meet confidence threshold\n`);

  // Step 4: Check which are already in memory
  const existing = await loadAllIncidents(config);
  const existingSymptoms = new Set(existing
    .filter(inc => inc.symptom && typeof inc.symptom === 'string')
    .map(inc => inc.symptom.toLowerCase().substring(0, 50))
  );

  // Filter out empty symptoms and duplicates
  const novel = filtered.filter(f =>
    f.symptom && f.symptom.length > 10 &&
    !existingSymptoms.has(f.symptom.toLowerCase().substring(0, 50))
  );

  console.log(`🆕 ${novel.length} are new (not already in memory)\n`);

  // Step 5: Convert to incidents and store
  const incidents: Incident[] = [];

  for (const finding of novel) {
    const incident = convertFindingToIncident(finding);
    incidents.push(incident);

    if (auto_store) {
      await storeIncident(incident, { validate_schema: true, config });
      const symptomPreview = incident.symptom ? incident.symptom.substring(0, 50) : 'Unknown';
      console.log(`✅ Stored: ${symptomPreview}...`);
    }
  }

  return incidents;
  });
}

/**
 * Find audit markdown files within date range
 */
async function findAuditFiles(days_back: number): Promise<string[]> {
  const AUDIT_DIR = getAuditDir();
  const cutoff = Date.now() - (days_back * 24 * 60 * 60 * 1000);
  const files: string[] = [];

  try {
    const entries = await fs.readdir(AUDIT_DIR, { withFileTypes: true });

    for (const entry of entries) {
      if (entry.isDirectory()) {
        // Check subdirectories (like RUN_xxx/)
        const subdir = path.join(AUDIT_DIR, entry.name);
        const subFiles = await fs.readdir(subdir);

        for (const subFile of subFiles) {
          if (subFile.endsWith('.md')) {
            const fullPath = path.join(subdir, subFile);
            const stats = await fs.stat(fullPath);

            if (stats.mtimeMs >= cutoff) {
              files.push(fullPath);
            }
          }
        }
      } else if (entry.name.endsWith('.md')) {
        // Direct markdown files in audit/
        const fullPath = path.join(AUDIT_DIR, entry.name);
        const stats = await fs.stat(fullPath);

        if (stats.mtimeMs >= cutoff) {
          files.push(fullPath);
        }
      }
    }
  } catch (error) {
    console.warn(`⚠️  Could not read audit directory: ${error}`);
  }

  return files;
}

/**
 * Extract incidents from a markdown file
 */
async function extractIncidentsFromFile(filePath: string): Promise<AuditFinding[]> {
  const findings: AuditFinding[] = [];

  try {
    const content = await fs.readFile(filePath, 'utf-8');
    const stats = await fs.stat(filePath);

    // Pattern 1: Root Cause Analysis documents
    if (content.includes('Root Cause') && content.includes('## ')) {
      const symptom = extractSection(content, ['Executive Summary', 'Symptom', 'Problem']);
      const root_cause = extractSection(content, ['Root Cause', 'PRIMARY ISSUE']);
      const fix = extractSection(content, ['Recommended Fix', 'Solution', 'Fix']);

      if (symptom && root_cause) {
        // Extract confidence score
        let confidence = 0.8; // Default
        const confMatch = content.match(/confidence[:\s]+(\d+)%/i);
        if (confMatch) {
          confidence = parseInt(confMatch[1]) / 100;
        }

        findings.push({
          file: filePath,
          symptom: cleanText(symptom),
          root_cause: cleanText(root_cause),
          fix: fix ? cleanText(fix) : undefined,
          confidence,
          timestamp: stats.mtimeMs
        });
      }
    }

    // Pattern 2: Implementation Error Tracker
    if (content.includes('Error Tracking') || content.includes('Error ID')) {
      const errors = extractErrorEntries(content);
      findings.push(...errors.map(e => ({
        ...e,
        file: filePath,
        timestamp: stats.mtimeMs
      })));
    }

    // Pattern 3: Fix Reports
    if (content.includes('FIX REPORT') || content.includes('FIXED')) {
      const symptom = extractSection(content, ['Problem', 'Issue', 'Bug']);
      const fix = extractSection(content, ['Fix', 'Solution', 'Resolution']);

      if (symptom && fix) {
        findings.push({
          file: filePath,
          symptom: cleanText(symptom),
          fix: cleanText(fix),
          confidence: 0.75,
          timestamp: stats.mtimeMs
        });
      }
    }

  } catch (error) {
    console.warn(`⚠️  Could not parse ${path.basename(filePath)}: ${error}`);
  }

  return findings;
}

/**
 * Extract a section from markdown content
 */
function extractSection(content: string, headers: string[]): string | null {
  for (const header of headers) {
    // Try ## Header format
    const regex1 = new RegExp(`##\\s*${header}[\\s\\S]*?\\n([\\s\\S]*?)(?=\\n##|$)`, 'i');
    const match1 = content.match(regex1);
    if (match1) {
      return match1[1].trim();
    }

    // Try **Header:** format
    const regex2 = new RegExp(`\\*\\*${header}[:\\s]*\\*\\*[\\s]*([\\s\\S]*?)(?=\\n\\n|\\n\\*\\*|$)`, 'i');
    const match2 = content.match(regex2);
    if (match2) {
      return match2[1].trim();
    }
  }

  return null;
}

/**
 * Extract error entries from error tracker documents
 */
function extractErrorEntries(content: string): Omit<AuditFinding, 'file' | 'timestamp'>[] {
  const findings: Omit<AuditFinding, 'file' | 'timestamp'>[] = [];

  // Match error blocks: ### E001: Title
  const errorBlocks = content.match(/###\s*E\d+:.*?(?=###|$)/gs);

  if (!errorBlocks) return findings;

  for (const block of errorBlocks) {
    // Extract title (symptom)
    const titleMatch = block.match(/###\s*E\d+:\s*(.+)/);
    if (!titleMatch) continue;

    const symptom = titleMatch[1].trim();

    // Extract root cause
    const rootCauseMatch = block.match(/####\s*Root Cause[\s\S]*?\n([^\#]+)/);
    const root_cause = rootCauseMatch ? cleanText(rootCauseMatch[1]) : undefined;

    // Extract fix/resolution
    const fixMatch = block.match(/####\s*(?:Resolution|Fix)[\s\S]*?\n([^\#]+)/);
    const fix = fixMatch ? cleanText(fixMatch[1]) : undefined;

    // Check if FIXED
    const isFixed = block.includes('✅ FIXED') || block.includes('RESOLVED');

    if (symptom && (root_cause || fix)) {
      findings.push({
        symptom,
        root_cause,
        fix,
        confidence: isFixed ? 0.9 : 0.7
      });
    }
  }

  return findings;
}

/**
 * Clean extracted text
 */
function cleanText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, '') // Remove code blocks
    .replace(/\[.*?\]\(.*?\)/g, '') // Remove markdown links
    .replace(/\*\*/g, '') // Remove bold
    .replace(/^[:：]\s*/g, '') // Remove leading colons (common in audit files)
    .replace(/\n+/g, ' ') // Collapse newlines
    .replace(/\s+/g, ' ') // Normalize whitespace
    .trim()
    .substring(0, 300); // Limit length
}

/**
 * Convert finding to incident
 */
function convertFindingToIncident(finding: AuditFinding): Incident {
  // Extract category from symptom/root_cause
  const category = inferCategory(finding.symptom, finding.root_cause);

  // Extract tags from symptom
  const tags = extractTags(finding.symptom, finding.root_cause);

  const incident: Incident = {
    incident_id: generateIncidentId(),
    timestamp: finding.timestamp,
    symptom: finding.symptom,
    symptom_type: category,
    root_cause: {
      description: finding.root_cause || 'Extracted from audit trail - details incomplete',
      category,
      confidence: finding.confidence
    },
    fix: {
      approach: finding.fix || 'See audit trail for details',
      changes: [],
      time_to_fix: 0
    },
    verification: {
      status: finding.fix ? 'partial' : 'unverified',
      regression_tests_passed: false,
      user_journey_tested: false,
      success_criteria_met: false
    },
    tags,
    files_changed: [],
    agent_used: 'audit-trail-miner',
    quality_gates: {
      guardian_validated: false,
      tested_e2e: false,
      tested_from_ui: false,
      security_reviewed: false,
      architect_reviewed: false
    },
    completeness: {
      symptom: true,
      root_cause: !!finding.root_cause,
      fix: !!finding.fix,
      verification: false,
      quality_score: finding.fix ? 0.5 : 0.3
    }
  };

  return incident;
}

/**
 * Infer category from text with improved detection
 */
function inferCategory(symptom: string, root_cause?: string): string {
  const text = `${symptom} ${root_cause || ''}`.toLowerCase();

  // React issues (check first - specific)
  if (text.includes('react') || text.includes('hook') || text.includes('render') || text.includes('component')) {
    return 'react-hooks';
  }

  // Database issues
  if (text.includes('database') || text.includes('prisma') || text.includes('schema') ||
      text.includes('migration') || text.includes('query') || text.includes('sql')) {
    return 'database';
  }

  // API issues
  if (text.includes('api') || text.includes('endpoint') || text.includes('route') || text.includes('request')) {
    return 'api';
  }

  // Performance issues
  if (text.includes('performance') || text.includes('slow') || text.includes('latency') ||
      text.includes('timeout') || text.includes('bottleneck')) {
    return 'performance';
  }

  // Search issues
  if (text.includes('search') || text.includes('vector') || text.includes('rerank')) {
    return 'search';
  }

  // Cache issues
  if (text.includes('cache') || text.includes('redis') || text.includes('memoiz')) {
    return 'caching';
  }

  // Error handling
  if (text.includes('error') || text.includes('exception') || text.includes('try-catch') || text.includes('throw')) {
    return 'error-handling';
  }

  // Validation
  if (text.includes('validation') || text.includes('invalid') || text.includes('format')) {
    return 'validation';
  }

  // Configuration
  if (text.includes('config') || text.includes('setting') || text.includes('env') || text.includes('environment')) {
    return 'config';
  }

  // Dependencies
  if (text.includes('dependency') || text.includes('import') || text.includes('module') || text.includes('package')) {
    return 'dependency';
  }

  // Authentication/Authorization
  if (text.includes('auth') || text.includes('permission') || text.includes('login') || text.includes('session')) {
    return 'authentication';
  }

  return 'unknown';
}

/**
 * Extract tags from text with improved coverage
 */
function extractTags(symptom: string, root_cause?: string): string[] {
  const text = `${symptom} ${root_cause || ''}`.toLowerCase();
  const tags: string[] = [];

  // Technology tags
  const techTags = [
    'react', 'nextjs', 'typescript', 'javascript',
    'api', 'rest', 'graphql',
    'groq', 'openai', 'llm',
    'sentry', 'monitoring',
    'prisma', 'postgresql', 'database',
    'redis', 'cache',
    'vercel', 'deployment'
  ];

  for (const tag of techTags) {
    if (text.includes(tag)) {
      tags.push(tag);
    }
  }

  // Problem type tags
  if (text.includes('error')) tags.push('error');
  if (text.includes('performance') || text.includes('slow') || text.includes('latency')) tags.push('performance');
  if (text.includes('hook')) tags.push('hooks');
  if (text.includes('render')) tags.push('rendering');
  if (text.includes('infinite') || text.includes('loop')) tags.push('infinite-loop');
  if (text.includes('crash') || text.includes('fail')) tags.push('crash');
  if (text.includes('timeout')) tags.push('timeout');
  if (text.includes('memory') || text.includes('leak')) tags.push('memory-leak');
  if (text.includes('race') || text.includes('concurren')) tags.push('concurrency');
  if (text.includes('deadlock')) tags.push('deadlock');
  if (text.includes('auth') || text.includes('permission')) tags.push('authentication');
  if (text.includes('validation')) tags.push('validation');
  if (text.includes('schema') || text.includes('migration')) tags.push('schema');
  if (text.includes('search') || text.includes('query')) tags.push('search');
  if (text.includes('cron') || text.includes('schedule')) tags.push('cron');

  // Always add audit-mined tag
  tags.push('audit-mined');

  // Add incomplete tag if root cause is missing or generic
  if (!root_cause || root_cause.length < 20) {
    tags.push('incomplete');
  }

  return [...new Set(tags)].slice(0, 10);
}

/**
 * Dry run - show what would be mined
 */
export async function previewAuditMining(days_back: number = 30, config?: MemoryConfig): Promise<void> {
  console.log('👀 Preview Audit Mining\n');
  console.log('═══════════════════════════════════════════════════════════════\n');

  const incidents = await mineAuditTrail({
    days_back,
    auto_store: false,
    min_confidence: 0.7,
    config
  });

  if (incidents.length === 0) {
    console.log('ℹ️  No new incidents found in audit trail\n');
    return;
  }

  console.log(`\n📋 Would add ${incidents.length} incidents to memory:\n`);

  incidents.forEach((inc, i) => {
    const symptomPreview = inc.symptom ? inc.symptom.substring(0, 60) : 'Unknown symptom';
    console.log(`${i + 1}. ${symptomPreview}...`);
    console.log(`   Category: ${inc.root_cause.category}`);
    console.log(`   Confidence: ${(inc.root_cause.confidence * 100).toFixed(0)}%`);
    console.log(`   Quality: ${(inc.completeness.quality_score * 100).toFixed(0)}%\n`);
  });

  console.log('💾 To store these incidents, run:');
  console.log('   mineAuditTrail({ days_back: 30, auto_store: true })\n');
}
