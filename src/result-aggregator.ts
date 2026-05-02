/**
 * Result Aggregator
 *
 * Combines and ranks results from multiple sources:
 * - Domain assessments from parallel assessors
 * - Memory retrieval (incidents and patterns)
 * - Trace correlations
 *
 * Uses unified scoring: 0.35*match + 0.25*confidence + 0.15*recency + 0.25*verification
 */

import type {
  DomainAssessment,
  AggregatedResult,
  CompactIncident,
  CompactPattern,
  ScoredItem
} from './types';

// Scoring weights
const SCORING_WEIGHTS = {
  match: 0.35,       // Similarity/match score
  confidence: 0.25,  // Assessment confidence
  recency: 0.15,     // How recent the incident/pattern
  verification: 0.25 // Verification status
};

// Verification status scores
const VERIFICATION_SCORES: Record<string, number> = {
  'V': 1.0,    // Verified
  'P': 0.6,    // Partial
  'U': 0.3     // Unverified
};

// Re-export ScoredItem from types
export type { ScoredItem } from './types';

export interface AggregationConfig {
  // Maximum results to return
  maxResults?: number;
  // Minimum score threshold (0-1)
  minScoreThreshold?: number;
  // Weight multiplier for verified items
  verificationBoost?: number;
  // Weight multiplier for recent items (last 7 days)
  recencyBoost?: number;
  // Deduplicate by similarity threshold
  dedupeThreshold?: number;
}

const DEFAULT_CONFIG: Required<AggregationConfig> = {
  maxResults: 10,
  minScoreThreshold: 0.3,
  verificationBoost: 1.2,
  recencyBoost: 1.1,
  dedupeThreshold: 0.8
};

/**
 * Calculate recency score (0-1) based on timestamp
 * Full score for last 24h, decays over 30 days
 */
function calculateRecencyScore(timestamp: number): number {
  const now = Date.now();
  const ageMs = now - timestamp;
  const dayMs = 24 * 60 * 60 * 1000;

  if (ageMs < dayMs) {
    return 1.0; // Last 24 hours
  }

  const ageDays = ageMs / dayMs;
  if (ageDays > 30) {
    return 0.1; // Older than 30 days
  }

  // Linear decay from 1.0 to 0.1 over 30 days
  return 1.0 - (ageDays / 30) * 0.9;
}

/**
 * Score a domain assessment
 */
export function scoreAssessment(
  assessment: DomainAssessment,
  config: AggregationConfig = {}
): ScoredItem {
  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  // Assessment-specific scoring
  // Match score based on probable causes count and specificity
  const matchScore = Math.min(1, assessment.probable_causes.length * 0.25 +
                                  assessment.related_incidents.length * 0.15);

  // Confidence from assessment
  const confidenceScore = assessment.confidence;

  // Recency: assume current (1.0) for live assessments
  const recencyScore = 1.0;

  // Verification: based on related incidents
  let verificationScore = 0.5; // Base score
  if (assessment.related_incidents.length > 0) {
    verificationScore = 0.7; // Has evidence
  }
  if (assessment.confidence >= 0.8) {
    verificationScore = 0.9; // High confidence
  }

  // Calculate weighted score
  let score = (
    SCORING_WEIGHTS.match * matchScore +
    SCORING_WEIGHTS.confidence * confidenceScore +
    SCORING_WEIGHTS.recency * recencyScore +
    SCORING_WEIGHTS.verification * verificationScore
  );

  // Apply verification boost if highly confident
  if (assessment.confidence >= 0.8) {
    score *= finalConfig.verificationBoost;
  }

  return {
    type: 'assessment',
    id: `assess_${assessment.domain}_${Date.now()}`,
    score: Math.min(1, score),
    domain: assessment.domain,
    summary: assessment.probable_causes[0] || 'Assessment completed',
    actions: assessment.recommended_actions,
    tags: assessment.search_tags,
    rawData: assessment
  };
}

/**
 * Score a compact incident from memory
 */
export function scoreIncident(
  incident: CompactIncident,
  config: AggregationConfig = {}
): ScoredItem {
  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  // Match score from similarity (if available)
  const matchScore = incident.sim || 0.5;

  // Confidence from root cause
  const confidenceScore = incident.rc.conf;

  // Recency score
  const recencyScore = calculateRecencyScore(incident.ts);

  // Verification status
  const verificationScore = VERIFICATION_SCORES[incident.v] || 0.3;

  // Calculate weighted score
  let score = (
    SCORING_WEIGHTS.match * matchScore +
    SCORING_WEIGHTS.confidence * confidenceScore +
    SCORING_WEIGHTS.recency * recencyScore +
    SCORING_WEIGHTS.verification * verificationScore
  );

  // Apply boosts
  if (incident.v === 'V') {
    score *= finalConfig.verificationBoost;
  }

  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  if (incident.ts > sevenDaysAgo) {
    score *= finalConfig.recencyBoost;
  }

  // Quality score bonus
  score += incident.q * 0.1;

  return {
    type: 'incident',
    id: incident.id,
    score: Math.min(1, score),
    domain: incident.rc.cat,
    summary: incident.sym,
    actions: [incident.fix.a],
    tags: incident.t,
    rawData: incident
  };
}

/**
 * Score a compact pattern from memory
 */
export function scorePattern(
  pattern: CompactPattern,
  matchScore: number = 0.5,
  config: AggregationConfig = {}
): ScoredItem {
  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  // Confidence from pattern (assume high since it's an extracted pattern)
  const confidenceScore = 0.8;

  // Recency from last seen
  const recencyScore = calculateRecencyScore(pattern.last);

  // Verification based on occurrence count
  let verificationScore = 0.5;
  if (pattern.n >= 3) {
    verificationScore = 0.8;
  } else if (pattern.n >= 2) {
    verificationScore = 0.65;
  }

  // Calculate weighted score
  let score = (
    SCORING_WEIGHTS.match * matchScore +
    SCORING_WEIGHTS.confidence * confidenceScore +
    SCORING_WEIGHTS.recency * recencyScore +
    SCORING_WEIGHTS.verification * verificationScore
  );

  // Frequency boost
  if (pattern.n >= 3) {
    score *= 1.15;
  }

  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  if (pattern.last > sevenDaysAgo) {
    score *= finalConfig.recencyBoost;
  }

  return {
    type: 'pattern',
    id: pattern.id,
    score: Math.min(1, score),
    domain: pattern.cat,
    summary: pattern.desc,
    actions: [pattern.fix],
    tags: pattern.t,
    rawData: pattern
  };
}

/**
 * Calculate similarity between two items for deduplication
 */
function calculateSimilarity(item1: ScoredItem, item2: ScoredItem): number {
  // Quick check: same type and domain?
  if (item1.type === item2.type && item1.domain === item2.domain) {
    // Check tag overlap
    const tags1 = new Set(item1.tags);
    const tags2 = new Set(item2.tags);
    const intersection = [...tags1].filter(t => tags2.has(t)).length;
    const union = new Set([...tags1, ...tags2]).size;
    const tagSimilarity = union > 0 ? intersection / union : 0;

    // Check summary similarity (simple word overlap)
    const words1 = new Set(item1.summary.toLowerCase().split(/\s+/));
    const words2 = new Set(item2.summary.toLowerCase().split(/\s+/));
    const wordIntersection = [...words1].filter(w => words2.has(w)).length;
    const wordUnion = new Set([...words1, ...words2]).size;
    const wordSimilarity = wordUnion > 0 ? wordIntersection / wordUnion : 0;

    return (tagSimilarity * 0.4 + wordSimilarity * 0.6);
  }

  return 0;
}

/**
 * Deduplicate items by similarity threshold
 */
function deduplicateItems(
  items: ScoredItem[],
  threshold: number
): ScoredItem[] {
  const result: ScoredItem[] = [];

  for (const item of items) {
    const isDuplicate = result.some(
      existing => calculateSimilarity(existing, item) >= threshold
    );

    if (!isDuplicate) {
      result.push(item);
    }
  }

  return result;
}

/**
 * Aggregate results from multiple sources
 */
export function aggregateResults(
  assessments: DomainAssessment[],
  incidents: CompactIncident[],
  patterns: CompactPattern[],
  config: AggregationConfig = {}
): AggregatedResult {
  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  // Score all items
  const scoredAssessments = assessments.map(a => scoreAssessment(a, config));
  const scoredIncidents = incidents.map(i => scoreIncident(i, config));
  const scoredPatterns = patterns.map(p => scorePattern(p, p.sim || 0.5, config));

  // Combine all scored items
  let allItems = [
    ...scoredAssessments,
    ...scoredIncidents,
    ...scoredPatterns
  ];

  // Filter by minimum score
  allItems = allItems.filter(item => item.score >= finalConfig.minScoreThreshold);

  // Sort by score descending
  allItems.sort((a, b) => b.score - a.score);

  // Deduplicate
  allItems = deduplicateItems(allItems, finalConfig.dedupeThreshold);

  // Limit results
  const topItems = allItems.slice(0, finalConfig.maxResults);

  // Build aggregated result
  const domains = [...new Set(topItems.map(i => i.domain).filter(Boolean) as string[])];
  const allTags = [...new Set(topItems.flatMap(i => i.tags))];

  // Calculate aggregate confidence
  const avgConfidence = topItems.length > 0
    ? topItems.reduce((sum, i) => sum + i.score, 0) / topItems.length
    : 0;

  // Compile recommended actions (deduplicated, ordered by source score)
  const actionSet = new Set<string>();
  const recommendedActions: string[] = [];
  for (const item of topItems) {
    for (const action of item.actions) {
      if (!actionSet.has(action)) {
        actionSet.add(action);
        recommendedActions.push(action);
      }
    }
  }

  return {
    items: topItems,
    total_count: allItems.length,
    domains_involved: domains,
    aggregate_confidence: avgConfidence,
    recommended_actions: recommendedActions.slice(0, 5),
    search_tags: allTags.slice(0, 10)
  };
}

/**
 * Format aggregated results for display
 */
export function formatAggregatedResults(result: AggregatedResult): string {
  const lines: string[] = [
    '## Aggregated Analysis Results',
    '',
    `**Domains:** ${result.domains_involved.join(', ') || 'General'}`,
    `**Confidence:** ${(result.aggregate_confidence * 100).toFixed(0)}%`,
    `**Total Matches:** ${result.total_count}`,
    '',
    '### Top Results',
    ''
  ];

  for (let i = 0; i < result.items.length; i++) {
    const item = result.items[i];
    const scoreBar = '█'.repeat(Math.round(item.score * 10)) +
                     '░'.repeat(10 - Math.round(item.score * 10));
    const typeIcon = item.type === 'assessment' ? '🔍' :
                     item.type === 'incident' ? '📋' : '🔄';

    lines.push(`${i + 1}. ${typeIcon} **${item.type}** [${scoreBar}] ${(item.score * 100).toFixed(0)}%`);
    lines.push(`   ${item.summary}`);
    if (item.domain) {
      lines.push(`   Domain: ${item.domain}`);
    }
    lines.push('');
  }

  lines.push('### Recommended Actions');
  lines.push('');

  for (let i = 0; i < result.recommended_actions.length; i++) {
    lines.push(`${i + 1}. ${result.recommended_actions[i]}`);
  }

  if (result.search_tags.length > 0) {
    lines.push('');
    lines.push(`**Tags:** ${result.search_tags.join(', ')}`);
  }

  return lines.join('\n');
}

/**
 * Create a quick summary for token-efficient output
 */
export function createQuickSummary(result: AggregatedResult): string {
  const topItem = result.items[0];
  if (!topItem) {
    return 'No relevant matches found.';
  }

  const confidence = (result.aggregate_confidence * 100).toFixed(0);
  const action = result.recommended_actions[0] || 'Investigate further';

  return `[${confidence}% confidence] ${topItem.summary}\nRecommended: ${action}`;
}
