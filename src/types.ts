/**
 * Debugging Memory System - Type Definitions
 *
 * Defines the structure of incidents, patterns, and memory operations.
 */

/**
 * Memory Configuration
 */
export interface MemoryConfig {
  storageMode: 'local' | 'shared';
  memoryPath: string;
  autoMine: boolean;
  defaultSimilarityThreshold: number;
  defaultMaxResults: number;
}

export interface Incident {
  // Identification
  incident_id: string;              // INC_YYYYMMDD_HHMMSS_random
  timestamp: number;                // Unix timestamp
  session_id?: string;              // Optional session reference

  // Symptom & Diagnosis
  symptom: string;                  // User-facing description
  symptom_type?: string;            // e.g., "search", "ui", "api"
  root_cause: RootCause;            // Detailed root cause

  // Fix & Resolution
  fix: Fix;                         // How it was fixed
  verification: Verification;       // How fix was verified

  // Context & Metadata
  tags: string[];                   // Categorization tags
  files_changed: string[];          // Files modified
  agent_used?: string;              // Which agent fixed it

  // Quality Indicators
  quality_gates: QualityGates;      // Validation status
  completeness: Completeness;       // Which fields are complete

  // Memory & Retrieval
  similarity_score?: number;        // When retrieved (0-1)
  pattern_id?: string;              // Associated pattern
  quality_score?: number;           // Overall quality (0-1)
  embedding?: number[];             // Semantic embedding for search
  patternized?: boolean;            // Whether included in extracted pattern

  // Durability Tracking
  durability?: DurabilityTracking;  // 30-day stability
}

export interface RootCause {
  description: string;              // What caused the issue
  file?: string;                    // Primary file involved
  line_range?: [number, number];   // Line numbers
  code_snippet?: string;            // Relevant code
  category: string;                 // e.g., "logic", "config", "dependency"
  confidence: number;               // 0-1, how confident in diagnosis
}

export interface Fix {
  approach: string;                 // High-level approach
  changes: FileChange[];            // Specific changes made
  pattern_used?: string;            // Pattern ID if applicable
  time_to_fix?: number;             // Minutes
}

export interface FileChange {
  file: string;                     // File path
  lines_changed: number;            // Number of lines
  change_type: 'add' | 'modify' | 'delete' | 'refactor';
  summary: string;                  // What changed
}

export interface Verification {
  status: 'verified' | 'partial' | 'unverified';
  regression_tests_passed: boolean;
  user_journey_tested: boolean;
  tests_run?: string[];             // Test files executed
  success_criteria_met: boolean;
}

export interface QualityGates {
  guardian_validated: boolean;      // Security check passed
  tested_e2e: boolean;              // End-to-end tested
  tested_from_ui: boolean;          // UI tested
  security_reviewed: boolean;       // Manual security review
  architect_reviewed: boolean;      // Design review
}

export interface Completeness {
  symptom: boolean;
  root_cause: boolean;
  fix: boolean;
  verification: boolean;
  quality_score: number;            // 0-1, overall completeness
}

export interface DurabilityTracking {
  fixed_at: number;                 // Unix timestamp
  last_checked: number;             // Unix timestamp
  still_working: boolean;           // True if stable
  days_stable: number;              // Days since fix
  recurrence_count: number;         // How many times issue returned
}

/**
 * Fix Pattern - Reusable solution template
 */
export interface Pattern {
  // Identification
  pattern_id: string;               // PTN_CATEGORY_NAME
  name: string;                     // Human-readable name
  description: string;              // What this pattern solves

  // Detection
  detection_signature: string[];    // Keywords/symptoms that match
  applicable_to: string[];          // Which agents/contexts can use

  // Solution
  solution_template: string;        // How to implement
  code_example?: string;            // Example code
  before_after?: BeforeAfter;       // Code comparison

  // Metadata
  tags: string[];                   // Categorization
  related_patterns: string[];       // Similar patterns

  // Success Tracking
  usage_history: UsageHistory;      // How often used
  success_rate: number;             // 0-1, success percentage
  last_used: number;                // Unix timestamp

  // Warnings
  caveats?: string[];               // Known limitations
  requires_validation?: string[];   // What to check
}

export interface BeforeAfter {
  before: string;                   // Code before fix
  after: string;                    // Code after fix
  explanation: string;              // Why this works
}

export interface UsageHistory {
  total_uses: number;               // Times applied
  successful_uses: number;          // Times it worked
  by_agent: Record<string, number>; // Usage per agent
  recent_incidents: string[];       // Recent incident IDs
}

/**
 * Memory Retrieval Configuration
 */
export interface RetrievalConfig {
  similarity_threshold: number;     // Min similarity (0-1)
  max_results: number;              // Max incidents to return
  temporal_preference?: number;     // Prefer recent (days)
  tags_filter?: string[];           // Only these tags
  agent_filter?: string;            // Only this agent
  include_unvalidated?: boolean;    // Include unvalidated incidents
}

/**
 * Memory Retrieval Result
 */
export interface RetrievalResult {
  incidents: Incident[];            // Matched incidents
  patterns: Pattern[];              // Matched patterns
  confidence: number;               // Overall match confidence
  retrieval_method: 'pattern' | 'incident' | 'hybrid';
  tokens_used: number;              // Context size estimate
}

/**
 * Session State (temporary during debugging)
 */
export interface DebugSession {
  session_id: string;               // SESSION_timestamp
  started_at: number;               // Unix timestamp
  symptom: string;                  // Current issue
  retrieved_context: RetrievalResult; // Memory retrieved
  current_hypothesis?: string;      // Active hypothesis
  status: 'active' | 'completed' | 'abandoned';
}

/**
 * Storage Options
 */
export interface StorageOptions {
  validate_schema?: boolean;        // Validate before storing
  extract_pattern?: boolean;        // Try to extract pattern
  calculate_similarity?: boolean;   // Compute embeddings
  mark_for_review?: boolean;        // Flag for manual review
}

/**
 * Verification Result
 */
export interface VerificationResult {
  valid: boolean;                   // Overall validity
  errors: string[];                 // Validation errors
  warnings: string[];               // Warnings (non-blocking)
}

// ============================================================================
// TOKEN OPTIMIZATION - Compact Types for LLM Context Injection
// ============================================================================

/**
 * Compact Incident - Token-efficient representation (~200 tokens vs ~550)
 *
 * Uses short keys to minimize token usage in LLM context.
 * Full incident can be loaded on-demand via incident_id.
 */
export interface CompactIncident {
  id: string;                       // incident_id
  ts: number;                       // timestamp
  sym: string;                      // symptom (max 80 chars)
  rc: {                             // root_cause (compact)
    d: string;                      // description (max 100 chars)
    cat: string;                    // category
    conf: number;                   // confidence (0-1)
  };
  fix: {                            // fix (compact)
    a: string;                      // approach (max 80 chars)
    n: number;                      // number of changes
  };
  v: 'V' | 'P' | 'U';              // verification: Verified/Partial/Unverified
  t: string[];                      // tags (max 5)
  q: number;                        // quality_score (0-1)
  sim?: number;                     // similarity_score (when retrieved)
}

/**
 * Compact Pattern - Token-efficient representation (~120 tokens vs ~250)
 */
export interface CompactPattern {
  id: string;                       // pattern_id
  n: number;                        // occurrence count (for scoring)
  desc: string;                     // description (max 100 chars)
  sig: string[];                    // detection_signature (max 5)
  fix: string;                      // solution approach (max 150 chars)
  sr: number;                       // success_rate (0-1)
  cat: string;                      // category
  t: string[];                      // tags (max 5)
  last: number;                     // last_used timestamp
  sim?: number;                     // similarity_score (when retrieved)
}

/**
 * Incident Summary - Minimal representation for quick scans (~100 tokens)
 */
export interface IncidentSummary {
  incident_id: string;
  symptom_preview: string;          // First 80 chars
  root_cause_preview: string;       // First 100 chars
  fix_preview: string;              // First 80 chars
  category: string;
  confidence: number;
  quality: number;
}

/**
 * Token Budget Configuration
 *
 * Controls how many tokens are allocated to different context sections.
 * Total default: 2500 tokens (leaving room for user prompt and response)
 */
export interface TokenBudget {
  total: number;                    // Total token budget (default: 2500)
  allocated: {
    patterns: number;               // 30% = 750 tokens
    incidents: number;              // 60% = 1500 tokens
    metadata: number;               // 10% = 250 tokens
  };
  perItem: {
    pattern: number;                // ~120 tokens per compact pattern
    incident: number;               // ~200 tokens per compact incident
    summary: number;                // ~100 tokens per summary
  };
}

/**
 * Default token budget configuration
 */
export const DEFAULT_TOKEN_BUDGET: TokenBudget = {
  total: 2500,
  allocated: {
    patterns: 750,
    incidents: 1500,
    metadata: 250,
  },
  perItem: {
    pattern: 120,
    incident: 200,
    summary: 100,
  },
};

/**
 * Field importance for selective serialization
 */
export type FieldImportance = 'critical' | 'secondary' | 'tertiary';

/**
 * Field configuration for selective inclusion
 */
export interface FieldConfig {
  field: string;
  importance: FieldImportance;
  maxLength?: number;
}

/**
 * Tiered Retrieval Configuration
 */
export interface TieredRetrievalConfig extends RetrievalConfig {
  tier: 'summary' | 'compact' | 'full';
  token_budget?: number;
}

/**
 * Tiered Retrieval Result
 */
export interface TieredRetrievalResult {
  // Summary tier
  summaries?: IncidentSummary[];
  pattern_summaries?: { id: string; name: string; success_rate: number }[];

  // Compact tier
  incidents?: CompactIncident[];
  patterns?: CompactPattern[];

  // Common
  confidence: number;
  tokens_used: number;
  has_more_details?: boolean;
  truncated?: {
    incidents: number;
    patterns: number;
  };
}

// ============================================================================
// PARALLEL ASSESSMENT - Domain-specific diagnosis types
// ============================================================================

/**
 * Assessment domains for parallel diagnosis
 */
export type AssessmentDomain = 'database' | 'frontend' | 'api' | 'performance';

/**
 * Domain Assessment Result
 */
export interface DomainAssessment {
  domain: string;                   // AssessmentDomain or string for flexibility
  symptom_classification: string;
  confidence: number;               // 0-1
  probable_causes: string[];
  recommended_actions: string[];
  related_incidents: string[];      // incident_ids
  search_tags: string[];
  execution_time_ms?: number;       // Optional timing
}

/**
 * Assessment summary for orchestration result
 */
export interface AssessmentSummary {
  domain: string;
  confidence: number;
  summary: string;
}

/**
 * Orchestration Result from parallel assessments
 */
export interface OrchestrationResult {
  symptom: string;
  domains_assessed: string[];
  assessments: AssessmentSummary[];
  priority_ranking: PriorityRank[];
  recommended_sequence: string[];
  total_execution_time_ms?: number;
  parallel_efficiency?: number;     // speedup factor vs sequential
}

/**
 * Priority ranking entry
 */
export interface PriorityRank {
  rank: number;
  domain: string;
  action: string;
  confidence?: number;
}

/**
 * Parallel search result
 */
export interface ParallelSearchResult {
  incident: Incident;
  score: number;
  matchType: 'exact' | 'tag' | 'fuzzy' | 'category' | 'semantic';
  highlights: string[];
}

/**
 * Parallel retrieval result
 */
export interface ParallelRetrievalResult {
  results: ParallelSearchResult[];
  strategies_used: string[];
  execution_time_ms: number;
  parallel_speedup: number;
}

/**
 * Scored item from result aggregation
 */
export interface ScoredItem {
  type: 'assessment' | 'incident' | 'pattern';
  id: string;
  score: number;
  domain?: string;
  summary: string;
  actions: string[];
  tags: string[];
  rawData: DomainAssessment | CompactIncident | CompactPattern;
}

/**
 * Aggregated result from multiple sources
 */
export interface AggregatedResult {
  items: ScoredItem[];
  total_count: number;
  domains_involved: string[];
  aggregate_confidence: number;
  recommended_actions: string[];
  search_tags: string[];
}

/**
 * Ranked results after aggregation
 */
export interface RankedResults {
  top_recommendations: AggregatedResult[];
  by_domain: Record<string, AggregatedResult[]>;
  by_confidence: AggregatedResult[];
  summary: ResultsSummary;
}

/**
 * Results summary statistics
 */
export interface ResultsSummary {
  total_results: number;
  domains_covered: string[];
  highest_confidence: number;
  has_verified_fix: boolean;
}

// ============================================================================
// TIERED STORAGE - Index & Summary types (v1.5.0)
// ============================================================================

/**
 * Verdict for search results - actionable classification
 */
export type SearchVerdict = 'KNOWN_FIX' | 'LIKELY_MATCH' | 'WEAK_SIGNAL' | 'NO_MATCH';

/**
 * Verdict-wrapped search result
 */
export interface VerdictResult {
  verdict: SearchVerdict;
  summary: string;
  confidence: number;
  incidents: CompactIncident[];
  patterns: CompactPattern[];
  tokens_used: number;
  action: string;
}

/**
 * Memory index for O(1) lookups without loading all files
 */
export interface MemoryIndex {
  version: number;
  last_updated: number;
  stats: {
    total_incidents: number;
    total_patterns: number;
    categories: Record<string, number>;
    tags: Record<string, number>;
    quality_distribution: { excellent: number; good: number; fair: number };
    oldest_timestamp: number;
    newest_timestamp: number;
  };
  by_category: Record<string, string[]>;   // category → incident_ids
  by_tag: Record<string, string[]>;         // tag → incident_ids
  by_file: Record<string, string[]>;        // file → incident_ids
  by_quality: { excellent: string[]; good: string[]; fair: string[] };
  recent: string[];                         // last 20 incident_ids (newest first)
}

/**
 * Incident JSONL entry for append-only log
 */
export interface IncidentLogEntry {
  incident_id: string;
  timestamp: number;
  symptom: string;
  category: string;
  tags: string[];
  quality_score: number;
  verification_status: string;
  files_changed: string[];
}

/**
 * Archive metadata for evicted incidents
 */
export interface ArchiveManifest {
  archived_at: number;
  incident_count: number;
  oldest_timestamp: number;
  newest_timestamp: number;
  reason: string;
}

// ============================================================================
// PROGRESSIVE DEPTH - Summary-first retrieval with drill-down
// ============================================================================

/**
 * A single match in progressive retrieval — one-liner with drill-down reference
 */
export interface ProgressiveMatch {
  id: string;                         // incident or pattern ID
  type: 'incident' | 'pattern';
  one_liner: string;                  // e.g. "React hook dep array → stale closure (85% conf)"
  verdict: SearchVerdict;
  confidence: number;
  detail_command: string;             // e.g. "/debugger-detail INC_xxx"
}

/**
 * Progressive retrieval result — compact matches with drill-down
 */
export interface ProgressiveResult {
  verdict: SearchVerdict;
  summary: string;
  matches: ProgressiveMatch[];
  total_matches: number;
  tokens_used: number;
  action: string;
}

// ============================================================================
// KEYWORD INDEX - Inverted index for scalable retrieval
// ============================================================================

/**
 * Inverted keyword index for O(1) candidate lookup
 */
export interface KeywordIndex {
  version: number;
  last_updated: number;
  keywords: Record<string, string[]>;  // keyword → incident_id[]
  total_incidents: number;
  total_keywords: number;
}

// ============================================================================
// OUTCOME TRACKING - Did the suggested fix actually work?
// ============================================================================

/**
 * Records whether a verdict/fix suggestion actually resolved the issue
 */
export interface VerdictOutcome {
  incident_id: string;
  session_id?: string;
  verdict_given: SearchVerdict;
  outcome: 'worked' | 'failed' | 'modified';
  recorded_at: number;
  notes?: string;
}
