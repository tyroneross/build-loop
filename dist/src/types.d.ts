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
    incident_id: string;
    timestamp: number;
    session_id?: string;
    symptom: string;
    symptom_type?: string;
    root_cause: RootCause;
    fix: Fix;
    verification: Verification;
    tags: string[];
    files_changed: string[];
    agent_used?: string;
    quality_gates: QualityGates;
    completeness: Completeness;
    similarity_score?: number;
    pattern_id?: string;
    quality_score?: number;
    embedding?: number[];
    patternized?: boolean;
    durability?: DurabilityTracking;
}
export interface RootCause {
    description: string;
    file?: string;
    line_range?: [number, number];
    code_snippet?: string;
    category: string;
    confidence: number;
}
export interface Fix {
    approach: string;
    changes: FileChange[];
    pattern_used?: string;
    time_to_fix?: number;
}
export interface FileChange {
    file: string;
    lines_changed: number;
    change_type: 'add' | 'modify' | 'delete' | 'refactor';
    summary: string;
}
export interface Verification {
    status: 'verified' | 'partial' | 'unverified';
    regression_tests_passed: boolean;
    user_journey_tested: boolean;
    tests_run?: string[];
    success_criteria_met: boolean;
}
export interface QualityGates {
    guardian_validated: boolean;
    tested_e2e: boolean;
    tested_from_ui: boolean;
    security_reviewed: boolean;
    architect_reviewed: boolean;
}
export interface Completeness {
    symptom: boolean;
    root_cause: boolean;
    fix: boolean;
    verification: boolean;
    quality_score: number;
}
export interface DurabilityTracking {
    fixed_at: number;
    last_checked: number;
    still_working: boolean;
    days_stable: number;
    recurrence_count: number;
}
/**
 * Fix Pattern - Reusable solution template
 */
export interface Pattern {
    pattern_id: string;
    name: string;
    description: string;
    detection_signature: string[];
    applicable_to: string[];
    solution_template: string;
    code_example?: string;
    before_after?: BeforeAfter;
    tags: string[];
    related_patterns: string[];
    usage_history: UsageHistory;
    success_rate: number;
    last_used: number;
    caveats?: string[];
    requires_validation?: string[];
}
export interface BeforeAfter {
    before: string;
    after: string;
    explanation: string;
}
export interface UsageHistory {
    total_uses: number;
    successful_uses: number;
    by_agent: Record<string, number>;
    recent_incidents: string[];
}
/**
 * Memory Retrieval Configuration
 */
export interface RetrievalConfig {
    similarity_threshold: number;
    max_results: number;
    temporal_preference?: number;
    tags_filter?: string[];
    agent_filter?: string;
    include_unvalidated?: boolean;
}
/**
 * Memory Retrieval Result
 */
export interface RetrievalResult {
    incidents: Incident[];
    patterns: Pattern[];
    confidence: number;
    retrieval_method: 'pattern' | 'incident' | 'hybrid';
    tokens_used: number;
}
/**
 * Session State (temporary during debugging)
 */
export interface DebugSession {
    session_id: string;
    started_at: number;
    symptom: string;
    retrieved_context: RetrievalResult;
    current_hypothesis?: string;
    status: 'active' | 'completed' | 'abandoned';
}
/**
 * Storage Options
 */
export interface StorageOptions {
    validate_schema?: boolean;
    extract_pattern?: boolean;
    calculate_similarity?: boolean;
    mark_for_review?: boolean;
}
/**
 * Verification Result
 */
export interface VerificationResult {
    valid: boolean;
    errors: string[];
    warnings: string[];
}
/**
 * Compact Incident - Token-efficient representation (~200 tokens vs ~550)
 *
 * Uses short keys to minimize token usage in LLM context.
 * Full incident can be loaded on-demand via incident_id.
 */
export interface CompactIncident {
    id: string;
    ts: number;
    sym: string;
    rc: {
        d: string;
        cat: string;
        conf: number;
    };
    fix: {
        a: string;
        n: number;
    };
    v: 'V' | 'P' | 'U';
    t: string[];
    q: number;
    sim?: number;
}
/**
 * Compact Pattern - Token-efficient representation (~120 tokens vs ~250)
 */
export interface CompactPattern {
    id: string;
    n: number;
    desc: string;
    sig: string[];
    fix: string;
    sr: number;
    cat: string;
    t: string[];
    last: number;
    sim?: number;
}
/**
 * Incident Summary - Minimal representation for quick scans (~100 tokens)
 */
export interface IncidentSummary {
    incident_id: string;
    symptom_preview: string;
    root_cause_preview: string;
    fix_preview: string;
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
    total: number;
    allocated: {
        patterns: number;
        incidents: number;
        metadata: number;
    };
    perItem: {
        pattern: number;
        incident: number;
        summary: number;
    };
}
/**
 * Default token budget configuration
 */
export declare const DEFAULT_TOKEN_BUDGET: TokenBudget;
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
    summaries?: IncidentSummary[];
    pattern_summaries?: {
        id: string;
        name: string;
        success_rate: number;
    }[];
    incidents?: CompactIncident[];
    patterns?: CompactPattern[];
    confidence: number;
    tokens_used: number;
    has_more_details?: boolean;
    truncated?: {
        incidents: number;
        patterns: number;
    };
}
/**
 * Assessment domains for parallel diagnosis
 */
export type AssessmentDomain = 'database' | 'frontend' | 'api' | 'performance';
/**
 * Domain Assessment Result
 */
export interface DomainAssessment {
    domain: string;
    symptom_classification: string;
    confidence: number;
    probable_causes: string[];
    recommended_actions: string[];
    related_incidents: string[];
    search_tags: string[];
    execution_time_ms?: number;
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
    parallel_efficiency?: number;
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
        quality_distribution: {
            excellent: number;
            good: number;
            fair: number;
        };
        oldest_timestamp: number;
        newest_timestamp: number;
    };
    by_category: Record<string, string[]>;
    by_tag: Record<string, string[]>;
    by_file: Record<string, string[]>;
    by_quality: {
        excellent: string[];
        good: string[];
        fair: string[];
    };
    recent: string[];
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
/**
 * A single match in progressive retrieval — one-liner with drill-down reference
 */
export interface ProgressiveMatch {
    id: string;
    type: 'incident' | 'pattern';
    one_liner: string;
    verdict: SearchVerdict;
    confidence: number;
    detail_command: string;
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
/**
 * Inverted keyword index for O(1) candidate lookup
 */
export interface KeywordIndex {
    version: number;
    last_updated: number;
    keywords: Record<string, string[]>;
    total_incidents: number;
    total_keywords: number;
}
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
//# sourceMappingURL=types.d.ts.map