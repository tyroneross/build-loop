/**
 * Batch Operations
 *
 * Batch commands for maintaining the memory system:
 * - Review incomplete incidents
 * - Extract patterns from existing incidents
 * - Clean up old sessions
 */

import prompts from 'prompts';
import fs from 'fs/promises';
import path from 'path';
import type { Incident, Pattern, MemoryConfig } from './types';
import {
  loadAllIncidents,
  storeIncident,
  generateIncidentId,
  validateIncident
} from './storage';
import { extractPatterns } from './pattern-extractor';
import { getMemoryPaths } from './config';
import { calculateQualityScore } from './interactive-verifier';

/**
 * Review and complete incomplete incidents interactively
 */
export async function batchReviewIncomplete(config?: MemoryConfig): Promise<void> {
  console.log('\n🔍 Batch Review: Incomplete Incidents');
  console.log('═══════════════════════════════════════════════\n');

  // Load all incidents
  const incidents = await loadAllIncidents(config);

  // Filter for incomplete incidents (low completeness score or marked incomplete)
  const incomplete = incidents.filter(inc => {
    const hasIncompleteTag = (inc.tags ?? []).includes('incomplete');
    const lowCompleteness = (inc.completeness?.quality_score ?? 0) < 0.7;
    const unverified = inc.verification?.status === 'unverified';

    return hasIncompleteTag || lowCompleteness || unverified;
  });

  if (incomplete.length === 0) {
    console.log('✅ No incomplete incidents found!\n');
    return;
  }

  console.log(`Found ${incomplete.length} incomplete incidents:\n`);

  let completed = 0;
  let skipped = 0;
  let deleted = 0;

  for (const [index, incident] of incomplete.entries()) {
    console.log(`\n─────────────────────────────────────────────────`);
    console.log(`Incident ${index + 1}/${incomplete.length}: ${incident.incident_id}`);
    console.log(`Symptom: ${incident.symptom}`);
    console.log(`Quality Score: ${((incident.completeness?.quality_score || 0) * 100).toFixed(0)}%`);
    console.log(`Verification: ${incident.verification?.status || 'unverified'}`);
    console.log(`Tags: [${incident.tags.join(', ')}]`);

    // Show what's missing
    const validation = validateIncident(incident);
    if (validation.warnings.length > 0) {
      console.log(`\n⚠️  Issues:`);
      validation.warnings.forEach(w => console.log(`   - ${w}`));
    }

    const response = await prompts({
      type: 'select',
      name: 'action',
      message: 'What would you like to do?',
      choices: [
        { title: 'Skip (review later)', value: 'skip' },
        { title: 'Complete now (interactive)', value: 'complete' },
        { title: 'Delete (invalid incident)', value: 'delete' },
        { title: 'Stop (exit batch review)', value: 'stop' }
      ]
    });

    if (!response.action || response.action === 'stop') {
      console.log('\n⏹️  Batch review stopped');
      break;
    }

    if (response.action === 'skip') {
      skipped++;
      continue;
    }

    if (response.action === 'delete') {
      const confirm = await prompts({
        type: 'confirm',
        name: 'value',
        message: 'Are you sure you want to delete this incident?',
        initial: false
      });

      if (confirm.value) {
        await deleteIncident(incident.incident_id, config);
        deleted++;
        console.log('✅ Incident deleted');
      } else {
        skipped++;
      }
      continue;
    }

    if (response.action === 'complete') {
      // Interactive completion
      const updated = await completeIncidentInteractive(incident);

      if (updated) {
        await storeIncident(updated, { config, validate_schema: true });
        completed++;
        console.log('✅ Incident updated and saved');
      } else {
        skipped++;
      }
    }
  }

  console.log('\n═══════════════════════════════════════════════');
  console.log('📊 Batch Review Summary:');
  console.log(`   Completed: ${completed}`);
  console.log(`   Skipped: ${skipped}`);
  console.log(`   Deleted: ${deleted}`);
  console.log('');
}

/**
 * Extract patterns from existing incidents in batch
 */
export async function batchExtractPatterns(options?: {
  category?: string;
  minIncidents?: number;
  config?: MemoryConfig;
}): Promise<Pattern[]> {
  const { category, minIncidents = 3, config } = options || {};

  console.log('\n🔍 Batch Pattern Extraction');
  console.log('═══════════════════════════════════════════════\n');

  if (category) {
    console.log(`   Category filter: ${category}`);
  }
  console.log(`   Minimum incidents: ${minIncidents}\n`);

  // Extract patterns
  const patterns = await extractPatterns({
    min_incidents: minIncidents,
    min_similarity: 0.6,
    auto_store: true,
    config
  });

  if (patterns.length === 0) {
    console.log('ℹ️  No patterns extracted. Need more similar incidents.\n');
    return [];
  }

  // Filter by category if specified
  const filtered = category
    ? patterns.filter(p => p.tags.includes(category.toLowerCase()))
    : patterns;

  console.log('\n═══════════════════════════════════════════════');
  console.log('📊 Pattern Extraction Summary:');
  console.log(`   Patterns extracted: ${filtered.length}`);
  console.log(`   Stored to disk: ${filtered.length}`);
  console.log('');

  return filtered;
}

/**
 * Clean up old sessions and empty incidents
 */
export async function batchCleanup(options?: {
  olderThanDays?: number;
  dryRun?: boolean;
  config?: MemoryConfig;
}): Promise<void> {
  const { olderThanDays = 90, dryRun = false, config } = options || {};

  console.log('\n🧹 Batch Cleanup');
  console.log('═══════════════════════════════════════════════\n');

  if (dryRun) {
    console.log('🔍 DRY RUN MODE - No files will be deleted\n');
  }

  const paths = getMemoryPaths(config);
  const cutoffTime = Date.now() - olderThanDays * 24 * 60 * 60 * 1000;
  const cutoffDate = new Date(cutoffTime);

  console.log(`Looking for sessions older than ${cutoffDate.toLocaleDateString()}...\n`);

  // Clean up sessions
  const sessionsDir = paths.sessions;
  let sessionsDeleted = 0;

  try {
    const sessionFiles = await fs.readdir(sessionsDir);

    for (const file of sessionFiles) {
      if (!file.endsWith('.json')) continue;

      const filepath = path.join(sessionsDir, file);
      const stats = await fs.stat(filepath);

      if (stats.mtimeMs < cutoffTime) {
        console.log(`   Old session: ${file} (${stats.mtime.toLocaleDateString()})`);

        if (!dryRun) {
          await fs.unlink(filepath);
          sessionsDeleted++;
        }
      }
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.error('Error cleaning sessions:', error);
    }
  }

  // Find incidents with quality issues
  const incidents = await loadAllIncidents(config);
  const problematic = incidents.filter(inc => {
    const tooOld = inc.timestamp < cutoffTime;
    const lowQuality = (inc.completeness?.quality_score ?? 0) < 0.4;
    const noTags = (inc.tags ?? []).length === 0;

    return tooOld && (lowQuality || noTags);
  });

  console.log(`\nFound ${problematic.length} problematic incidents:`);
  problematic.forEach(inc => {
    const date = new Date(inc.timestamp);
    const dateStr = isNaN(date.getTime()) ? 'Unknown' : date.toLocaleDateString();
    const score = inc.completeness?.quality_score ?? 0;
    console.log(`   ${inc.incident_id} - ${dateStr} - Score: ${(score * 100).toFixed(0)}%`);
  });

  if (problematic.length > 0 && !dryRun) {
    const confirm = await prompts({
      type: 'confirm',
      name: 'value',
      message: `Delete ${problematic.length} low-quality incidents?`,
      initial: false
    });

    if (confirm.value) {
      for (const inc of problematic) {
        await deleteIncident(inc.incident_id, config);
      }
      console.log(`✅ Deleted ${problematic.length} incidents`);
    }
  }

  console.log('\n═══════════════════════════════════════════════');
  console.log('📊 Cleanup Summary:');
  console.log(`   Sessions deleted: ${dryRun ? 0 : sessionsDeleted}`);
  console.log(`   Incidents deleted: ${dryRun ? 0 : problematic.length}`);

  if (dryRun) {
    console.log('\n💡 Run without --dry-run to actually delete files');
  }
  console.log('');
}

/**
 * Complete incident interactively
 */
async function completeIncidentInteractive(incident: Incident): Promise<Incident | null> {
  console.log('\n📝 Complete Incident Interactively\n');

  // Check what's missing
  const needsRootCause = !incident.root_cause.description || incident.root_cause.confidence < 0.5;
  const needsFix = !incident.fix.approach || incident.fix.changes.length === 0;
  const needsVerification = incident.verification.status === 'unverified';

  if (needsRootCause) {
    const rootCauseResponse = await prompts([
      {
        type: 'text',
        name: 'description',
        message: 'Root cause description:',
        initial: incident.root_cause.description || ''
      },
      {
        type: 'text',
        name: 'category',
        message: 'Category (e.g., react-hooks, api, config):',
        initial: incident.root_cause.category || ''
      },
      {
        type: 'number',
        name: 'confidence',
        message: 'Confidence (0-1):',
        initial: incident.root_cause.confidence || 0.8,
        min: 0,
        max: 1,
        increment: 0.1
      }
    ]);

    if (rootCauseResponse.description) {
      incident.root_cause = {
        ...incident.root_cause,
        description: rootCauseResponse.description,
        category: rootCauseResponse.category,
        confidence: rootCauseResponse.confidence
      };
    }
  }

  if (needsFix) {
    const fixResponse = await prompts([
      {
        type: 'text',
        name: 'approach',
        message: 'Fix approach:',
        initial: incident.fix.approach || ''
      },
      {
        type: 'list',
        name: 'files',
        message: 'Files changed (comma-separated):',
        initial: incident.files_changed.join(', ')
      }
    ]);

    if (fixResponse.approach) {
      incident.fix.approach = fixResponse.approach;

      if (fixResponse.files) {
        const files = fixResponse.files.split(',').map((f: string) => f.trim()).filter(Boolean);
        incident.files_changed = files;
        incident.fix.changes = files.map((file: string) => ({
          file,
          lines_changed: 10,
          change_type: 'modify' as const,
          summary: 'Updated'
        }));
      }
    }
  }

  if (needsVerification) {
    const verifyResponse = await prompts({
      type: 'select',
      name: 'status',
      message: 'Verification status:',
      choices: [
        { title: 'Verified', value: 'verified' },
        { title: 'Partially verified', value: 'partial' },
        { title: 'Unverified', value: 'unverified' }
      ]
    });

    if (verifyResponse.status) {
      incident.verification.status = verifyResponse.status;
      incident.verification.success_criteria_met = verifyResponse.status === 'verified';
    }
  }

  // Remove incomplete tag
  incident.tags = incident.tags.filter(t => t !== 'incomplete');

  // Recalculate completeness
  incident.completeness = {
    symptom: !!incident.symptom,
    root_cause: !!incident.root_cause.description && incident.root_cause.confidence >= 0.5,
    fix: !!incident.fix.approach && incident.fix.changes.length > 0,
    verification: incident.verification.status !== 'unverified',
    quality_score: calculateQualityScore(incident)
  };

  return incident;
}

/**
 * Delete an incident file
 */
async function deleteIncident(incident_id: string, config?: MemoryConfig): Promise<void> {
  const paths = getMemoryPaths(config);
  const filepath = path.join(paths.incidents, `${incident_id}.json`);

  try {
    await fs.unlink(filepath);
  } catch (error) {
    console.error(`Failed to delete ${incident_id}:`, error);
  }
}
