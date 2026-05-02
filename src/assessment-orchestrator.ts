/**
 * Assessment Orchestrator
 *
 * Coordinates parallel domain-specific assessments for comprehensive debugging.
 * Detects which domains are involved based on symptom keywords and launches
 * appropriate assessors in parallel.
 */

import type { DomainAssessment, OrchestrationResult } from './types';

// Domain keyword mappings for symptom analysis
const DOMAIN_KEYWORDS: Record<string, string[]> = {
  database: [
    'query', 'schema', 'migration', 'prisma', 'sql', 'slow query', 'connection',
    'constraint', 'database', 'postgresql', 'postgres', 'mysql', 'mongodb',
    'index', 'foreign key', 'transaction', 'pool', 'timeout', 'deadlock'
  ],
  frontend: [
    'react', 'hook', 'useeffect', 'usestate', 'render', 'component', 'ui',
    'state', 'hydration', 'client', 'browser', 'dom', 'css', 'style',
    'redux', 'zustand', 'context', 'props', 'rerender', 'infinite loop'
  ],
  api: [
    'endpoint', 'route', 'request', 'response', 'auth', '500', '404', '401',
    'rest', 'graphql', 'middleware', 'api', 'cors', 'jwt', 'token',
    'session', 'header', 'body', 'payload', 'fetch', 'axios'
  ],
  performance: [
    'slow', 'latency', 'timeout', 'memory', 'leak', 'cpu', 'bottleneck',
    'performance', 'optimization', 'n+1', 'cache', 'bundle', 'load time',
    'blocking', 'async', 'lag', 'freeze', 'hang', 'unresponsive'
  ]
};

// Priority weights for different domains
const DOMAIN_PRIORITY_WEIGHTS: Record<string, number> = {
  database: 0.9,      // Database issues often root cause
  api: 0.85,          // API errors are common
  frontend: 0.8,      // Frontend issues visible but often symptoms
  performance: 0.75   // Performance often cross-cutting
};

export type Domain = 'database' | 'frontend' | 'api' | 'performance';
export type DomainPriority = 'high' | 'medium' | 'low';

export interface DomainDetection {
  domain: Domain;
  priority: DomainPriority;
  matchCount: number;
  matchedKeywords: string[];
}

export interface OrchestrationConfig {
  // Minimum keyword matches for inclusion
  minMatchThreshold?: number;
  // Maximum domains to assess in parallel
  maxParallelDomains?: number;
  // Whether to include low-priority domains
  includeLowPriority?: boolean;
  // Custom domain weights
  domainWeights?: Partial<Record<Domain, number>>;
}

const DEFAULT_CONFIG: Required<OrchestrationConfig> = {
  minMatchThreshold: 1,
  maxParallelDomains: 4,
  includeLowPriority: false,
  domainWeights: DOMAIN_PRIORITY_WEIGHTS
};

/**
 * Detect which domains are potentially involved based on symptom keywords
 */
export function detectDomains(
  symptom: string,
  config: OrchestrationConfig = {}
): DomainDetection[] {
  const normalizedSymptom = symptom.toLowerCase();
  const finalConfig = { ...DEFAULT_CONFIG, ...config };
  const detections: DomainDetection[] = [];

  for (const [domain, keywords] of Object.entries(DOMAIN_KEYWORDS)) {
    const matchedKeywords: string[] = [];

    for (const keyword of keywords) {
      if (normalizedSymptom.includes(keyword.toLowerCase())) {
        matchedKeywords.push(keyword);
      }
    }

    const matchCount = matchedKeywords.length;
    let priority: DomainPriority;

    if (matchCount >= 2) {
      priority = 'high';
    } else if (matchCount >= 1) {
      priority = 'medium';
    } else {
      priority = 'low';
    }

    detections.push({
      domain: domain as Domain,
      priority,
      matchCount,
      matchedKeywords
    });
  }

  // Sort by match count descending, then by domain weight
  return detections.sort((a, b) => {
    if (a.matchCount !== b.matchCount) {
      return b.matchCount - a.matchCount;
    }
    const weightA = finalConfig.domainWeights[a.domain] || 0.5;
    const weightB = finalConfig.domainWeights[b.domain] || 0.5;
    return weightB - weightA;
  });
}

/**
 * Select which domains should be assessed based on detection results
 */
export function selectDomainsForAssessment(
  detections: DomainDetection[],
  config: OrchestrationConfig = {}
): Domain[] {
  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  // Filter by priority
  let selectedDetections = detections.filter(d => {
    if (d.priority === 'low' && !finalConfig.includeLowPriority) {
      return false;
    }
    return d.matchCount >= finalConfig.minMatchThreshold;
  });

  // If no domains match, assess all (vague symptom)
  if (selectedDetections.length === 0) {
    selectedDetections = detections;
  }

  // Limit to max parallel domains
  return selectedDetections
    .slice(0, finalConfig.maxParallelDomains)
    .map(d => d.domain);
}

/**
 * Format domain detection results for the orchestrator agent
 */
export function formatDetectionForAgent(detections: DomainDetection[]): string {
  const lines = ['## Domain Analysis', ''];

  for (const detection of detections) {
    const priorityEmoji = detection.priority === 'high' ? '🔴' :
                          detection.priority === 'medium' ? '🟡' : '⚪';

    lines.push(`${priorityEmoji} **${detection.domain}** (${detection.priority})`);
    if (detection.matchedKeywords.length > 0) {
      lines.push(`   Keywords: ${detection.matchedKeywords.join(', ')}`);
    }
    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Generate prompts for each domain assessor agent
 */
export function generateAssessorPrompts(
  symptom: string,
  domains: Domain[]
): Record<Domain, string> {
  const prompts: Partial<Record<Domain, string>> = {};

  for (const domain of domains) {
    prompts[domain] = `Assess the following symptom for ${domain}-related issues:

**Symptom:** ${symptom}

Follow your assessment process:
1. Classify the symptom type
2. Search debugging memory for similar incidents
3. Analyze context specific to ${domain}
4. Generate a JSON assessment with confidence score

Return ONLY the JSON assessment object.`;
  }

  return prompts as Record<Domain, string>;
}

/**
 * Parse assessment JSON from agent response
 */
export function parseAssessmentResponse(
  response: string,
  domain: Domain
): DomainAssessment | null {
  try {
    // Try to extract JSON from response
    const jsonMatch = response.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return null;
    }

    const parsed = JSON.parse(jsonMatch[0]);

    // Validate required fields
    if (!parsed.confidence || !parsed.probable_causes) {
      return null;
    }

    return {
      domain,
      symptom_classification: parsed.symptom_classification || 'unknown',
      confidence: Math.min(1, Math.max(0, parsed.confidence)),
      probable_causes: parsed.probable_causes || [],
      recommended_actions: parsed.recommended_actions || [],
      related_incidents: parsed.related_incidents || [],
      search_tags: parsed.search_tags || []
    };
  } catch {
    return null;
  }
}

/**
 * Rank assessments by confidence and evidence
 */
export function rankAssessments(
  assessments: DomainAssessment[]
): DomainAssessment[] {
  return assessments.sort((a, b) => {
    // Primary: confidence
    if (a.confidence !== b.confidence) {
      return b.confidence - a.confidence;
    }

    // Secondary: evidence count (related incidents)
    const evidenceA = a.related_incidents.length;
    const evidenceB = b.related_incidents.length;
    if (evidenceA !== evidenceB) {
      return evidenceB - evidenceA;
    }

    // Tertiary: domain priority weight
    const weightA = DOMAIN_PRIORITY_WEIGHTS[a.domain] || 0.5;
    const weightB = DOMAIN_PRIORITY_WEIGHTS[b.domain] || 0.5;
    return weightB - weightA;
  });
}

/**
 * Generate priority ranking for orchestration result
 */
export function generatePriorityRanking(
  rankedAssessments: DomainAssessment[]
): Array<{ rank: number; domain: Domain; action: string }> {
  return rankedAssessments.map((assessment, index) => ({
    rank: index + 1,
    domain: assessment.domain as Domain,
    action: assessment.recommended_actions[0] || 'Investigate further'
  }));
}

/**
 * Generate recommended sequence of actions
 */
export function generateRecommendedSequence(
  rankedAssessments: DomainAssessment[]
): string[] {
  const sequence: string[] = [];

  for (const assessment of rankedAssessments) {
    // Add top 2 actions from each assessment
    for (const action of assessment.recommended_actions.slice(0, 2)) {
      if (!sequence.includes(action)) {
        sequence.push(action);
      }
    }
  }

  // Limit to top 5 actions
  return sequence.slice(0, 5);
}

/**
 * Create the final orchestration result
 */
export function createOrchestrationResult(
  symptom: string,
  assessments: DomainAssessment[]
): OrchestrationResult {
  const rankedAssessments = rankAssessments(assessments);

  return {
    symptom,
    domains_assessed: assessments.map(a => a.domain as Domain),
    assessments: rankedAssessments.map(a => ({
      domain: a.domain,
      confidence: a.confidence,
      summary: a.probable_causes[0] || 'No specific cause identified'
    })),
    priority_ranking: generatePriorityRanking(rankedAssessments),
    recommended_sequence: generateRecommendedSequence(rankedAssessments)
  };
}

/**
 * Format orchestration result for display
 */
export function formatOrchestrationResult(result: OrchestrationResult): string {
  const lines: string[] = [
    '## Parallel Assessment Results',
    '',
    `**Symptom:** ${result.symptom}`,
    '',
    `**Domains Assessed:** ${result.domains_assessed.join(', ')}`,
    '',
    '### Assessment Summary',
    ''
  ];

  for (const assessment of result.assessments) {
    const confidenceBar = '█'.repeat(Math.round(assessment.confidence * 10)) +
                          '░'.repeat(10 - Math.round(assessment.confidence * 10));
    lines.push(`**${assessment.domain}** [${confidenceBar}] ${(assessment.confidence * 100).toFixed(0)}%`);
    lines.push(`   ${assessment.summary}`);
    lines.push('');
  }

  lines.push('### Priority Ranking');
  lines.push('');

  for (const item of result.priority_ranking) {
    lines.push(`${item.rank}. **${item.domain}**: ${item.action}`);
  }

  lines.push('');
  lines.push('### Recommended Sequence');
  lines.push('');

  for (let i = 0; i < result.recommended_sequence.length; i++) {
    lines.push(`${i + 1}. ${result.recommended_sequence[i]}`);
  }

  return lines.join('\n');
}

/**
 * Main orchestration entry point
 * Note: This prepares the orchestration but doesn't spawn agents directly.
 * The actual agent spawning is done by Claude Code using the Task tool.
 */
export function prepareOrchestration(
  symptom: string,
  config: OrchestrationConfig = {}
): {
  detections: DomainDetection[];
  selectedDomains: Domain[];
  prompts: Record<Domain, string>;
  shouldUseOrchestrator: boolean;
} {
  const detections = detectDomains(symptom, config);
  const selectedDomains = selectDomainsForAssessment(detections, config);
  const prompts = generateAssessorPrompts(symptom, selectedDomains);

  // Determine if orchestrator is needed (multiple domains) or direct assessment
  const highPriorityDomains = detections.filter(d => d.priority === 'high');
  const shouldUseOrchestrator = highPriorityDomains.length !== 1 || selectedDomains.length > 1;

  return {
    detections,
    selectedDomains,
    prompts,
    shouldUseOrchestrator
  };
}
