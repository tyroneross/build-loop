/**
 * Debug With Memory - Wrapper Function
 *
 * Automatically checks memory before debugging and stores incidents after.
 * Works with all 4 entry points: chief agent, direct agent, chat mode, resume work.
 */

import type { Incident, RetrievalResult, Pattern, MemoryConfig } from './types';
import { checkMemory } from './retrieval';
import { storeIncident, generateIncidentId, getMemoryStats } from './storage';
import { autoExtractPatternIfReady } from './pattern-extractor';
import { getMemoryPaths } from './config';
import fs from 'fs/promises';
import path from 'path';

interface DebugContext {
  symptom: string;
  memory: RetrievalResult;
  session_id: string;
  started_at: number;
}

interface DebugResult {
  success: boolean;
  incident?: Incident;
  memory_stored: boolean;
  verification_passed: boolean;
  context_used: DebugContext;
}

/**
 * Main wrapper function for debugging with memory
 *
 * Usage:
 *   const result = await debugWithMemory("Search filters not working");
 *
 * This function:
 * 1. Checks memory for similar past incidents
 * 2. Provides context to the debugging process
 * 3. Stores the incident after debugging
 * 4. Verifies storage succeeded
 */
export async function debugWithMemory(
  symptom: string,
  options: {
    agent?: string;
    auto_store?: boolean;
    min_confidence?: number;
  } = {}
): Promise<DebugResult> {

  const {
    agent = 'unknown',
    auto_store = true,
    min_confidence = 0.7
  } = options;

  console.log('\n🧠 Debug With Memory');
  console.log('═══════════════════════════════════════════════\n');

  // Step 1: Check Memory
  console.log('Step 1: Checking memory for similar incidents...');

  const memory = await checkMemory(symptom, {
    similarity_threshold: min_confidence,
    max_results: 5,
    temporal_preference: 90
  });

  const context: DebugContext = {
    symptom,
    memory,
    session_id: `SESSION_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
    started_at: Date.now()
  };

  // Save session context
  await saveSessionContext(context);

  // Step 2: Present Memory to User/Agent
  if (memory.patterns.length > 0) {
    console.log(`✅ Found ${memory.patterns.length} matching patterns`);
    console.log(`   Confidence: ${(memory.confidence * 100).toFixed(0)}%`);
    console.log(`   Context tokens: ${memory.tokens_used}\n`);

    memory.patterns.forEach((pattern, i) => {
      console.log(`   Pattern ${i + 1}: ${pattern.name}`);
      console.log(`   Success rate: ${(pattern.success_rate * 100).toFixed(0)}%`);
      console.log(`   Solution: ${pattern.solution_template.substring(0, 80)}...`);
      if (pattern.caveats && pattern.caveats.length > 0) {
        console.log(`   ⚠️  Caveats: ${pattern.caveats.join(', ')}`);
      }
      console.log('');
    });
  } else if (memory.incidents.length > 0) {
    console.log(`✅ Found ${memory.incidents.length} similar incidents`);
    console.log(`   Confidence: ${(memory.confidence * 100).toFixed(0)}%`);
    console.log(`   Context tokens: ${memory.tokens_used}\n`);

    memory.incidents.forEach((incident, i) => {
      console.log(`   Incident ${i + 1}: ${incident.symptom?.substring(0, 60) ?? 'Unknown'}...`);
      console.log(`   Root cause: ${incident.root_cause?.description?.substring(0, 60) ?? 'Unknown'}...`);
      console.log(`   Fix approach: ${incident.fix?.approach?.substring(0, 60) ?? 'Unknown'}...`);
      console.log(`   Confidence: ${((incident.root_cause?.confidence ?? 0) * 100).toFixed(0)}%`);

      // Quality warnings
      if (incident.quality_gates && !incident.quality_gates.guardian_validated) {
        console.log(`   ⚠️  Not security validated`);
      }
      if (incident.verification && incident.verification.status !== 'verified') {
        console.log(`   ⚠️  Fix not fully verified`);
      }
      console.log('');
    });
  } else {
    console.log('ℹ️  No similar incidents found in memory');
    console.log('   This appears to be a new type of issue\n');
  }

  console.log('Step 2: Proceed with debugging...');
  console.log('   (Memory context available for reference)\n');

  // Return context for manual debugging
  // In actual usage, this would be followed by agent invocation or manual debugging
  return {
    success: true,
    memory_stored: false,
    verification_passed: false,
    context_used: context
  };
}

/**
 * Store incident after debugging is complete
 *
 * Usage:
 *   await storeDebugIncident(session_id, {
 *     root_cause: { description: "...", confidence: 0.9 },
 *     fix: { approach: "...", changes: [...] },
 *     // ... other fields
 *   });
 */
export async function storeDebugIncident(
  session_id: string,
  incident_data: Partial<Incident>
): Promise<{ success: boolean; incident_id: string; verified: boolean }> {

  console.log('\n💾 Storing incident in memory...');

  // Load session context
  const context = await loadSessionContext(session_id);
  if (!context) {
    console.error('❌ Session not found');
    return { success: false, incident_id: '', verified: false };
  }

  // Build complete incident (with category-prefixed ID)
  const category = incident_data.root_cause?.category;
  const incident: Incident = {
    incident_id: generateIncidentId(category),
    timestamp: Date.now(),
    symptom: context.symptom,
    session_id: session_id,

    // Provided by caller
    root_cause: incident_data.root_cause!,
    fix: incident_data.fix!,
    verification: incident_data.verification || {
      status: 'unverified',
      regression_tests_passed: false,
      user_journey_tested: false,
      success_criteria_met: false
    },

    // Metadata
    tags: incident_data.tags || extractTags(context.symptom),
    files_changed: incident_data.files_changed || [],
    agent_used: incident_data.agent_used || 'unknown',

    // Quality tracking
    quality_gates: incident_data.quality_gates || {
      guardian_validated: false,
      tested_e2e: false,
      tested_from_ui: false,
      security_reviewed: false,
      architect_reviewed: false
    },

    completeness: calculateCompleteness(incident_data)
  };

  // Store incident
  try {
    const result = await storeIncident(incident, { validate_schema: true });
    console.log(`✅ Stored: ${result.incident_id}`);

    // Verify storage (ground truth check)
    const verified = await verifyIncidentStorage(result.incident_id);

    if (verified) {
      console.log('✅ Verification passed: File exists on disk');
    } else {
      console.warn('⚠️  Verification failed: File not found on disk');
    }

    // Auto-extract patterns if ready
    console.log('\n🔍 Checking for pattern extraction opportunities...');
    const pattern = await autoExtractPatternIfReady(incident);

    if (pattern) {
      console.log(`✨ Auto-extracted pattern: ${pattern.name}`);
      console.log(`📊 Based on ${pattern.usage_history.total_uses} similar incidents`);
      console.log(`🎯 Pattern ID: ${pattern.pattern_id}`);
      console.log(`✅ Success rate: ${(pattern.success_rate * 100).toFixed(0)}%`);
    } else {
      console.log('ℹ️  Not enough similar incidents yet for pattern extraction');
    }

    // Clean up session
    await cleanupSession(session_id);

    return {
      success: true,
      incident_id: result.incident_id,
      verified
    };

  } catch (error) {
    console.error('❌ Storage failed:', error);
    return { success: false, incident_id: '', verified: false };
  }
}

/**
 * Verify incident was actually stored (file system ground truth)
 */
async function verifyIncidentStorage(incident_id: string, config?: MemoryConfig): Promise<boolean> {
  const paths = getMemoryPaths(config);
  const filepath = path.join(paths.incidents, `${incident_id}.json`);

  try {
    await fs.access(filepath);
    const content = await fs.readFile(filepath, 'utf-8');
    const incident = JSON.parse(content);
    return incident.incident_id === incident_id;
  } catch {
    return false;
  }
}

/**
 * Save session context for later reference
 */
async function saveSessionContext(context: DebugContext, config?: MemoryConfig): Promise<void> {
  const paths = getMemoryPaths(config);
  await fs.mkdir(paths.sessions, { recursive: true });
  const filepath = path.join(paths.sessions, `${context.session_id}.json`);
  await fs.writeFile(filepath, JSON.stringify(context, null, 2), 'utf-8');
}

/**
 * Load session context
 */
async function loadSessionContext(session_id: string, config?: MemoryConfig): Promise<DebugContext | null> {
  const paths = getMemoryPaths(config);
  const filepath = path.join(paths.sessions, `${session_id}.json`);

  try {
    const content = await fs.readFile(filepath, 'utf-8');
    return JSON.parse(content) as DebugContext;
  } catch {
    return null;
  }
}

/**
 * Clean up session after incident stored
 */
async function cleanupSession(session_id: string, config?: MemoryConfig): Promise<void> {
  const paths = getMemoryPaths(config);
  const filepath = path.join(paths.sessions, `${session_id}.json`);

  try {
    await fs.unlink(filepath);
  } catch {
    // Ignore cleanup errors
  }
}

/**
 * Extract tags from symptom description
 */
function extractTags(symptom: string): string[] {
  const keywords = symptom.toLowerCase().match(/\b\w{4,}\b/g) || [];
  const commonWords = new Set(['this', 'that', 'with', 'from', 'have', 'been', 'were']);

  return [...new Set(keywords.filter(w => !commonWords.has(w)))].slice(0, 5);
}

/**
 * Calculate incident completeness score
 */
function calculateCompleteness(incident_data: Partial<Incident>): Incident['completeness'] {
  const fields = {
    symptom: true,
    root_cause: !!incident_data.root_cause,
    fix: !!incident_data.fix,
    verification: !!incident_data.verification
  };

  const total = Object.values(fields).length;
  const complete = Object.values(fields).filter(Boolean).length;

  return {
    ...fields,
    quality_score: complete / total
  };
}

/**
 * Get memory system statistics
 */
export async function getMemoryStatus(): Promise<void> {
  console.log('\n📊 Memory System Status');
  console.log('═══════════════════════════════════════════════\n');

  const stats = await getMemoryStats();

  console.log(`Total incidents: ${stats.total_incidents}`);
  console.log(`Total patterns: ${stats.total_patterns}`);
  console.log(`Disk usage: ${stats.disk_usage_kb} KB`);

  if (stats.total_incidents > 0 && stats.oldest_incident > 0) {
    const oldestDate = new Date(stats.oldest_incident);
    const newestDate = new Date(stats.newest_incident);
    const oldestStr = isNaN(oldestDate.getTime()) ? 'Unknown' : oldestDate.toLocaleDateString();
    const newestStr = isNaN(newestDate.getTime()) ? 'Unknown' : newestDate.toLocaleDateString();
    console.log(`Oldest incident: ${oldestStr}`);
    console.log(`Newest incident: ${newestStr}`);
  }

  console.log('');
}
