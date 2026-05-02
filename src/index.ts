/**
 * Claude Memory - Main Entry Point
 *
 * Debugging memory system for Claude Code.
 * Never solve the same bug twice.
 */

// Configuration
export { getConfig, getMemoryPaths, displayConfig } from './config';
export type { MemoryConfig } from './types';

// Core Types
export type {
  Incident,
  Pattern,
  RootCause,
  Fix,
  FileChange,
  Verification,
  QualityGates,
  Completeness,
  DurabilityTracking,
  RetrievalConfig,
  RetrievalResult,
  StorageOptions,
  VerificationResult
} from './types';

// Storage Operations
export {
  storeIncident,
  loadIncident,
  loadAllIncidents,
  storePattern,
  loadPattern,
  loadAllPatterns,
  validateIncident,
  generateIncidentId,
  generatePatternId,
  getMemoryStats,
  // v1.5.0 additions
  appendToIncidentLog,
  searchIncidentLog,
  rebuildIndex,
  loadIndex,
  buildMemorySummary,
  archiveOldIncidents,
  compressContext,
  enforceTokenBudget,
  toCompactIncident,
  toCompactPattern,
  toCompactIncidents,
  toCompactPatterns,
  // v1.6.0 additions
  updateKeywordIndex,
  loadKeywordIndex,
  findCandidatesByKeyword,
  rebuildKeywordIndex,
  updateIndexIncremental,
  recordOutcome,
  loadOutcomes,
  getOutcomeStats,
} from './storage';

// Interactive Verification
export {
  buildIncidentInteractive,
  calculateQualityScore,
  generateQualityFeedback
} from './interactive-verifier';

// Retrieval Operations
export {
  checkMemory,
  getFullIncident,
  searchByTags,
  getRecentIncidents,
  enhancedSearch
} from './retrieval';
export type { SearchResult } from './retrieval';

// Main Debugging Wrapper
// Note: These will be added when we copy debug-with-memory.ts
export { debugWithMemory, storeDebugIncident, getMemoryStatus } from './debug-wrapper';

// Pattern Extraction
export { extractPatterns, suggestPatterns, autoExtractPatternIfReady } from './pattern-extractor';

// Batch Operations
export { batchReviewIncomplete, batchExtractPatterns, batchCleanup } from './batch-operations';

// Audit Trail Mining
export { mineAuditTrail, previewAuditMining } from './audit-miner';

// Token-Efficient Retrieval
export {
  checkMemoryTiered,
  quickMemoryCheck,
  getIncidentDetails,
  getPatternDetails,
  // v1.5.0 verdict system
  checkMemoryWithVerdict,
  classifyVerdict,
  // v1.6.0 progressive + scaled
  checkMemoryProgressive,
  checkMemoryScaled,
} from './retrieval';

export type {
  CompactIncident,
  CompactPattern,
  IncidentSummary,
  TokenBudget,
  TieredRetrievalConfig,
  TieredRetrievalResult,
  // v1.5.0 types
  SearchVerdict,
  VerdictResult,
  MemoryIndex,
  IncidentLogEntry,
  ArchiveManifest,
  // v1.6.0 types
  ProgressiveResult,
  ProgressiveMatch,
  KeywordIndex,
  VerdictOutcome,
} from './types';

// Parallel Retrieval
export {
  parallelSearch,
  parallelPatternMatch,
  parallelMemoryCheck
} from './parallel-retrieval';

// Assessment Orchestration
export {
  detectDomains,
  selectDomainsForAssessment,
  generateAssessorPrompts,
  parseAssessmentResponse,
  rankAssessments,
  createOrchestrationResult,
  formatOrchestrationResult,
  prepareOrchestration
} from './assessment-orchestrator';

export type {
  Domain,
  DomainPriority,
  DomainDetection,
  OrchestrationConfig
} from './assessment-orchestrator';

export type {
  DomainAssessment,
  OrchestrationResult,
  AggregatedResult,
  ScoredItem
} from './types';

// Result Aggregation
export {
  scoreAssessment,
  scoreIncident,
  scorePattern,
  aggregateResults,
  formatAggregatedResults,
  createQuickSummary
} from './result-aggregator';

// Context Engine (v1.6.0)
export {
  generateDynamicSection,
  generateSessionContext,
  checkFileContext,
} from './context-engine';

// Trace Ingestion (v1.4.0)
export * from './traces';

// Internal Logger (v1.8.0)
export { traced, log, readLogs, configureLogger } from './logger';
export type { LogLevel, LogEntry, LoggerConfig, ReadLogsOptions } from './logger';

// Log Reader (v1.8.0)
export { readProjectLogs } from './log-reader';
export type { LogSource, LogSeverity, LogReaderOptions, LogItem, LogReaderResult } from './log-reader';
