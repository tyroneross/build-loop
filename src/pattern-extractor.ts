/**
 * Pattern Extractor
 *
 * Automatically detects when 3+ similar incidents can be extracted into
 * a reusable pattern. Helps prevent solving the same problem repeatedly.
 */

import type { Incident, Pattern, MemoryConfig } from './types';
import { loadAllIncidents, storePattern, generatePatternId } from './storage';
import { loadAllPatterns } from './storage';
import { traced } from './logger';

interface PatternCandidate {
  category: string;
  incidents: Incident[];
  commonality_score: number;
  tags_overlap: string[];
  files_overlap: string[];
}

/**
 * Analyze incidents and extract reusable patterns
 */
export async function extractPatterns(options: {
  min_incidents?: number;
  min_similarity?: number;
  auto_store?: boolean;
  config?: MemoryConfig;
} = {}): Promise<Pattern[]> {
  return traced('patterns:extractPatterns', { min: options.min_incidents }, async () => {

  const {
    min_incidents = 3,
    min_similarity = 0.7,
    auto_store = false,
    config
  } = options;

  console.log('🔍 Pattern Extraction Analysis\n');
  console.log(`   Minimum incidents: ${min_incidents}`);
  console.log(`   Minimum similarity: ${(min_similarity * 100).toFixed(0)}%\n`);

  const incidents = await loadAllIncidents(config);
  const existingPatterns = await loadAllPatterns(config);

  console.log(`📚 Analyzing ${incidents.length} incidents...\n`);

  // Step 1: Group incidents by category
  const categorized = groupByCategory(incidents);

  // Step 2: Find pattern candidates
  const candidates: PatternCandidate[] = [];

  for (const [category, categoryIncidents] of Object.entries(categorized)) {
    if (categoryIncidents.length < min_incidents) continue;

    // Check if incidents share common characteristics
    const commonality = calculateCommonality(categoryIncidents);

    if (commonality.score >= min_similarity) {
      candidates.push({
        category,
        incidents: categoryIncidents,
        commonality_score: commonality.score,
        tags_overlap: commonality.common_tags,
        files_overlap: commonality.common_files
      });
    }
  }

  console.log(`✅ Found ${candidates.length} pattern candidates:\n`);

  // Step 3: Create patterns from candidates
  const patterns: Pattern[] = [];

  for (const candidate of candidates) {
    console.log(`📋 Pattern Candidate: ${candidate.category.toUpperCase()}`);
    console.log(`   Incidents: ${candidate.incidents.length}`);
    console.log(`   Commonality: ${(candidate.commonality_score * 100).toFixed(0)}%`);
    console.log(`   Shared tags: [${candidate.tags_overlap.join(', ')}]`);
    console.log(`   Shared files: ${candidate.files_overlap.length}\n`);

    // Check if pattern already exists
    const patternId = generatePatternId(candidate.category, 'common_fix');
    const exists = existingPatterns.some(p => p.pattern_id === patternId);

    if (exists) {
      console.log(`   ⚠️  Pattern already exists: ${patternId}\n`);
      continue;
    }

    const pattern = createPatternFromIncidents(candidate);
    patterns.push(pattern);

    if (auto_store) {
      await storePattern(pattern, config);
      console.log(`   ✅ Stored pattern: ${pattern.pattern_id}\n`);
    }
  }

  return patterns;
  });
}

/**
 * Group incidents by root cause category
 */
function groupByCategory(incidents: Incident[]): Record<string, Incident[]> {
  const groups: Record<string, Incident[]> = {};

  for (const incident of incidents) {
    const category = incident.root_cause.category || 'unknown';
    if (!groups[category]) {
      groups[category] = [];
    }
    groups[category].push(incident);
  }

  return groups;
}

/**
 * Calculate commonality among incidents
 */
function calculateCommonality(incidents: Incident[]): {
  score: number;
  common_tags: string[];
  common_files: string[];
} {
  if (incidents.length === 0) {
    return { score: 0, common_tags: [], common_files: [] };
  }

  // Find tags that appear in most incidents
  const tagCounts = new Map<string, number>();
  for (const inc of incidents) {
    for (const tag of (inc.tags ?? [])) {
      tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
    }
  }

  const threshold = Math.ceil(incidents.length * 0.6); // 60% of incidents
  const common_tags = Array.from(tagCounts.entries())
    .filter(([_, count]) => count >= threshold)
    .map(([tag, _]) => tag);

  // Find files that appear in multiple incidents
  const fileCounts = new Map<string, number>();
  for (const inc of incidents) {
    for (const file of (inc.files_changed ?? [])) {
      fileCounts.set(file, (fileCounts.get(file) || 0) + 1);
    }
  }

  const common_files = Array.from(fileCounts.entries())
    .filter(([_, count]) => count >= 2) // At least 2 incidents
    .map(([file, _]) => file);

  // Calculate overall commonality score
  // React-hooks: 6 common tags / 9 total = 67% → good pattern!
  // Error-handling: 3 common tags / 7 total = 43% → decent pattern
  const tag_similarity = common_tags.length / Math.max(tagCounts.size, 1);
  const file_overlap = common_files.length > 0 ? 0.15 : 0;
  const incident_density = incidents.length >= 5 ? 0.15 : (incidents.length >= 3 ? 0.1 : 0);

  // More generous scoring: high tag similarity is the main signal
  const score = (tag_similarity * 0.7) + file_overlap + incident_density;

  return {
    score: Math.min(score, 1.0),
    common_tags,
    common_files
  };
}

/**
 * Create a Pattern from a group of similar incidents
 */
function createPatternFromIncidents(candidate: PatternCandidate): Pattern {
  const { category, incidents, tags_overlap } = candidate;

  // Extract most common symptoms/causes
  const detection_signature = extractDetectionSignature(incidents);
  const solution_template = synthesizeSolution(incidents);
  const code_example = extractBestCodeExample(incidents);

  // Calculate success rate
  const verified = incidents.filter(i => i.verification.status === 'verified').length;
  const success_rate = verified / incidents.length;

  // Build usage history
  const usage_history = {
    total_uses: incidents.length,
    successful_uses: verified,
    by_agent: countByAgent(incidents),
    recent_incidents: incidents.slice(-5).map(i => i.incident_id)
  };

  // Extract caveats from incidents
  const caveats = extractCaveats(incidents);

  const pattern: Pattern = {
    pattern_id: generatePatternId(category, 'common_fix'),
    name: generatePatternName(category, incidents),
    description: generatePatternDescription(category, incidents),
    detection_signature,
    applicable_to: ['coder', 'frontend-ui', 'database', 'tester'],
    solution_template,
    code_example,
    tags: tags_overlap,
    related_patterns: [],
    usage_history,
    success_rate,
    last_used: Math.max(...incidents.map(i => i.timestamp)),
    caveats
  };

  return pattern;
}

/**
 * Extract detection signature (keywords for matching)
 */
function extractDetectionSignature(incidents: Incident[]): string[] {
  const keywords = new Set<string>();

  for (const incident of incidents) {
    // Extract from symptom (with null safety)
    if (incident.symptom && typeof incident.symptom === 'string') {
      const symptomWords = incident.symptom.toLowerCase()
        .split(/\s+/)
        .filter(w => w.length > 3);

      symptomWords.forEach(w => keywords.add(w));
    }

    // Extract from tags
    (incident.tags ?? []).forEach(tag => keywords.add(tag.toLowerCase()));

    // Extract from category
    keywords.add(incident.root_cause.category);
  }

  // Return most frequent keywords
  return Array.from(keywords).slice(0, 10);
}

/**
 * Synthesize solution template from incidents
 */
function synthesizeSolution(incidents: Incident[]): string {
  // Find most common approach
  const approaches = incidents.map(i => i.fix.approach);

  // Simple heuristic: longest approach is usually most detailed
  const bestApproach = approaches.reduce((longest, current) =>
    current.length > longest.length ? current : longest
  , '');

  // Generalize the solution
  return `Common pattern observed across ${incidents.length} incidents:\n\n${bestApproach}\n\nVerify this approach applies to your specific case before implementing.`;
}

/**
 * Extract best code example from incidents
 */
function extractBestCodeExample(incidents: Incident[]): string | undefined {
  // Find incident with highest confidence and code snippet
  const withCode = incidents.filter(i => i.root_cause.code_snippet);

  if (withCode.length === 0) return undefined;

  const best = withCode.reduce((highest, current) =>
    current.root_cause.confidence > highest.root_cause.confidence ? current : highest
  );

  return best.root_cause.code_snippet;
}

/**
 * Count incident usage by agent
 */
function countByAgent(incidents: Incident[]): Record<string, number> {
  const counts: Record<string, number> = {};

  for (const incident of incidents) {
    const agent = incident.agent_used || 'unknown';
    counts[agent] = (counts[agent] || 0) + 1;
  }

  return counts;
}

/**
 * Extract caveats from incidents
 */
function extractCaveats(incidents: Incident[]): string[] {
  const caveats: string[] = [];

  // Check if any incidents had issues
  const unverified = incidents.filter(i => i.verification.status !== 'verified');
  if (unverified.length > 0) {
    caveats.push(`${unverified.length}/${incidents.length} applications were not fully verified`);
  }

  // Check if any had low quality scores
  const lowQuality = incidents.filter(i => i.completeness.quality_score < 0.8);
  if (lowQuality.length > 0) {
    caveats.push('Some incidents have incomplete information - verify before applying');
  }

  // Check if pattern is recent or old
  const oldestTimestamp = Math.min(...incidents.map(i => i.timestamp));
  const ageInDays = (Date.now() - oldestTimestamp) / (24 * 60 * 60 * 1000);

  if (ageInDays > 90) {
    caveats.push('Pattern based on incidents older than 90 days - codebase may have changed');
  }

  return caveats;
}

/**
 * Generate human-readable pattern name
 */
function generatePatternName(category: string, incidents: Incident[]): string {
  const categoryNames: Record<string, string> = {
    'react-hooks': 'React Hook Dependency Issues',
    'error-handling': 'Error Handling Pattern',
    'api': 'API Error Pattern',
    'dependency': 'Module Dependency Issues',
    'validation': 'Input Validation Pattern',
    'config': 'Configuration Management',
    'performance': 'Performance Optimization'
  };

  return categoryNames[category] || `${category.charAt(0).toUpperCase()}${category.slice(1)} Pattern`;
}

/**
 * Generate pattern description
 */
function generatePatternDescription(category: string, incidents: Incident[]): string {
  const avgTime = incidents.reduce((sum, i) => sum + (i.fix.time_to_fix || 0), 0) / incidents.length;

  return `This pattern has been successfully applied ${incidents.length} times to resolve ${category} issues. Average time to fix: ${Math.round(avgTime)} minutes. Success rate: ${((incidents.filter(i => i.verification.status === 'verified').length / incidents.length) * 100).toFixed(0)}%.`;
}

/**
 * Auto-extract pattern if enough similar incidents exist
 * Called automatically after storing an incident
 */
export async function autoExtractPatternIfReady(
  newIncident: Incident,
  options: {
    minSimilar?: number;
    minQuality?: number;
    config?: MemoryConfig;
  } = {}
): Promise<Pattern | null> {
  return traced('patterns:autoExtract', { category: newIncident.root_cause.category }, async () => {

  const {
    minSimilar = 3,
    minQuality = 0.75,
    config
  } = options;

  // Step 1: Find similar incidents (same category, confidence >0.7)
  const allIncidents = await loadAllIncidents(config);
  const category = newIncident.root_cause.category;

  const similarIncidents = allIncidents.filter(inc =>
    inc.root_cause.category === category &&
    inc.root_cause.confidence >= 0.7 &&
    !inc.patternized // Don't include incidents already in patterns
  );

  // Step 2: Check if we have enough similar incidents
  if (similarIncidents.length < minSimilar) {
    return null;
  }

  // Step 3: Calculate commonality and quality
  const commonality = calculateCommonality(similarIncidents);

  // Step 4: Validate quality score
  if (commonality.score < minQuality) {
    console.log(`   📊 Commonality score: ${(commonality.score * 100).toFixed(0)}% (need ${(minQuality * 100).toFixed(0)}%)`);
    return null;
  }

  // Step 5: Create pattern candidate
  const candidate: PatternCandidate = {
    category,
    incidents: similarIncidents,
    commonality_score: commonality.score,
    tags_overlap: commonality.common_tags,
    files_overlap: commonality.common_files
  };

  // Step 6: Check if pattern already exists
  const existingPatterns = await loadAllPatterns(config);
  const patternId = generatePatternId(category, 'common_fix');
  const exists = existingPatterns.some(p => p.pattern_id === patternId);

  if (exists) {
    return null; // Pattern already extracted
  }

  // Step 7: Create and store pattern
  const pattern = createPatternFromIncidents(candidate);
  await storePattern(pattern, config);

  // Step 8: Tag incidents with pattern_id and mark as patternized
  const { storeIncident: updateIncident } = await import('./storage');
  for (const incident of similarIncidents) {
    incident.pattern_id = pattern.pattern_id;
    incident.patternized = true;
    await updateIncident(incident, { config });
  }

  return pattern;
  });
}

/**
 * Suggest patterns to extract (dry run)
 */
export async function suggestPatterns(config?: MemoryConfig): Promise<void> {
  console.log('💡 Pattern Extraction Suggestions\n');
  console.log('═══════════════════════════════════════════════════════════════\n');

  const patterns = await extractPatterns({
    min_incidents: 3,
    min_similarity: 0.6,
    auto_store: false,
    config
  });

  if (patterns.length === 0) {
    console.log('ℹ️  No patterns detected yet. Need at least 3 similar incidents.\n');
    return;
  }

  console.log(`\n📊 Extraction Summary:\n`);
  console.log(`   Patterns detected: ${patterns.length}`);
  console.log(`   Ready to extract: ${patterns.length}\n`);

  patterns.forEach((pattern, i) => {
    console.log(`${i + 1}. ${pattern.name}`);
    console.log(`   ID: ${pattern.pattern_id}`);
    console.log(`   Incidents: ${pattern.usage_history.total_uses}`);
    console.log(`   Success rate: ${(pattern.success_rate * 100).toFixed(0)}%`);
    console.log(`   Tags: [${pattern.tags.slice(0, 5).join(', ')}]`);
    if (pattern.caveats && pattern.caveats.length > 0) {
      console.log(`   ⚠️  Caveats: ${pattern.caveats.length}`);
    }
    console.log('');
  });

  console.log('💾 To store these patterns, run:');
  console.log('   extractPatterns({ min_incidents: 3, auto_store: true })\n');
}
