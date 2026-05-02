"use strict";
/**
 * Claude Memory - Main Entry Point
 *
 * Debugging memory system for Claude Code.
 * Never solve the same bug twice.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.batchCleanup = exports.batchExtractPatterns = exports.batchReviewIncomplete = exports.autoExtractPatternIfReady = exports.suggestPatterns = exports.extractPatterns = exports.getMemoryStatus = exports.storeDebugIncident = exports.debugWithMemory = exports.enhancedSearch = exports.getRecentIncidents = exports.searchByTags = exports.getFullIncident = exports.checkMemory = exports.generateQualityFeedback = exports.calculateQualityScore = exports.buildIncidentInteractive = exports.getOutcomeStats = exports.loadOutcomes = exports.recordOutcome = exports.updateIndexIncremental = exports.rebuildKeywordIndex = exports.findCandidatesByKeyword = exports.loadKeywordIndex = exports.updateKeywordIndex = exports.toCompactPatterns = exports.toCompactIncidents = exports.toCompactPattern = exports.toCompactIncident = exports.enforceTokenBudget = exports.compressContext = exports.archiveOldIncidents = exports.buildMemorySummary = exports.loadIndex = exports.rebuildIndex = exports.searchIncidentLog = exports.appendToIncidentLog = exports.getMemoryStats = exports.generatePatternId = exports.generateIncidentId = exports.validateIncident = exports.loadAllPatterns = exports.loadPattern = exports.storePattern = exports.loadAllIncidents = exports.loadIncident = exports.storeIncident = exports.displayConfig = exports.getMemoryPaths = exports.getConfig = void 0;
exports.readProjectLogs = exports.configureLogger = exports.readLogs = exports.log = exports.traced = exports.checkFileContext = exports.generateSessionContext = exports.generateDynamicSection = exports.createQuickSummary = exports.formatAggregatedResults = exports.aggregateResults = exports.scorePattern = exports.scoreIncident = exports.scoreAssessment = exports.prepareOrchestration = exports.formatOrchestrationResult = exports.createOrchestrationResult = exports.rankAssessments = exports.parseAssessmentResponse = exports.generateAssessorPrompts = exports.selectDomainsForAssessment = exports.detectDomains = exports.parallelMemoryCheck = exports.parallelPatternMatch = exports.parallelSearch = exports.checkMemoryScaled = exports.checkMemoryProgressive = exports.classifyVerdict = exports.checkMemoryWithVerdict = exports.getPatternDetails = exports.getIncidentDetails = exports.quickMemoryCheck = exports.checkMemoryTiered = exports.previewAuditMining = exports.mineAuditTrail = void 0;
// Configuration
var config_1 = require("./config");
Object.defineProperty(exports, "getConfig", { enumerable: true, get: function () { return config_1.getConfig; } });
Object.defineProperty(exports, "getMemoryPaths", { enumerable: true, get: function () { return config_1.getMemoryPaths; } });
Object.defineProperty(exports, "displayConfig", { enumerable: true, get: function () { return config_1.displayConfig; } });
// Storage Operations
var storage_1 = require("./storage");
Object.defineProperty(exports, "storeIncident", { enumerable: true, get: function () { return storage_1.storeIncident; } });
Object.defineProperty(exports, "loadIncident", { enumerable: true, get: function () { return storage_1.loadIncident; } });
Object.defineProperty(exports, "loadAllIncidents", { enumerable: true, get: function () { return storage_1.loadAllIncidents; } });
Object.defineProperty(exports, "storePattern", { enumerable: true, get: function () { return storage_1.storePattern; } });
Object.defineProperty(exports, "loadPattern", { enumerable: true, get: function () { return storage_1.loadPattern; } });
Object.defineProperty(exports, "loadAllPatterns", { enumerable: true, get: function () { return storage_1.loadAllPatterns; } });
Object.defineProperty(exports, "validateIncident", { enumerable: true, get: function () { return storage_1.validateIncident; } });
Object.defineProperty(exports, "generateIncidentId", { enumerable: true, get: function () { return storage_1.generateIncidentId; } });
Object.defineProperty(exports, "generatePatternId", { enumerable: true, get: function () { return storage_1.generatePatternId; } });
Object.defineProperty(exports, "getMemoryStats", { enumerable: true, get: function () { return storage_1.getMemoryStats; } });
// v1.5.0 additions
Object.defineProperty(exports, "appendToIncidentLog", { enumerable: true, get: function () { return storage_1.appendToIncidentLog; } });
Object.defineProperty(exports, "searchIncidentLog", { enumerable: true, get: function () { return storage_1.searchIncidentLog; } });
Object.defineProperty(exports, "rebuildIndex", { enumerable: true, get: function () { return storage_1.rebuildIndex; } });
Object.defineProperty(exports, "loadIndex", { enumerable: true, get: function () { return storage_1.loadIndex; } });
Object.defineProperty(exports, "buildMemorySummary", { enumerable: true, get: function () { return storage_1.buildMemorySummary; } });
Object.defineProperty(exports, "archiveOldIncidents", { enumerable: true, get: function () { return storage_1.archiveOldIncidents; } });
Object.defineProperty(exports, "compressContext", { enumerable: true, get: function () { return storage_1.compressContext; } });
Object.defineProperty(exports, "enforceTokenBudget", { enumerable: true, get: function () { return storage_1.enforceTokenBudget; } });
Object.defineProperty(exports, "toCompactIncident", { enumerable: true, get: function () { return storage_1.toCompactIncident; } });
Object.defineProperty(exports, "toCompactPattern", { enumerable: true, get: function () { return storage_1.toCompactPattern; } });
Object.defineProperty(exports, "toCompactIncidents", { enumerable: true, get: function () { return storage_1.toCompactIncidents; } });
Object.defineProperty(exports, "toCompactPatterns", { enumerable: true, get: function () { return storage_1.toCompactPatterns; } });
// v1.6.0 additions
Object.defineProperty(exports, "updateKeywordIndex", { enumerable: true, get: function () { return storage_1.updateKeywordIndex; } });
Object.defineProperty(exports, "loadKeywordIndex", { enumerable: true, get: function () { return storage_1.loadKeywordIndex; } });
Object.defineProperty(exports, "findCandidatesByKeyword", { enumerable: true, get: function () { return storage_1.findCandidatesByKeyword; } });
Object.defineProperty(exports, "rebuildKeywordIndex", { enumerable: true, get: function () { return storage_1.rebuildKeywordIndex; } });
Object.defineProperty(exports, "updateIndexIncremental", { enumerable: true, get: function () { return storage_1.updateIndexIncremental; } });
Object.defineProperty(exports, "recordOutcome", { enumerable: true, get: function () { return storage_1.recordOutcome; } });
Object.defineProperty(exports, "loadOutcomes", { enumerable: true, get: function () { return storage_1.loadOutcomes; } });
Object.defineProperty(exports, "getOutcomeStats", { enumerable: true, get: function () { return storage_1.getOutcomeStats; } });
// Interactive Verification
var interactive_verifier_1 = require("./interactive-verifier");
Object.defineProperty(exports, "buildIncidentInteractive", { enumerable: true, get: function () { return interactive_verifier_1.buildIncidentInteractive; } });
Object.defineProperty(exports, "calculateQualityScore", { enumerable: true, get: function () { return interactive_verifier_1.calculateQualityScore; } });
Object.defineProperty(exports, "generateQualityFeedback", { enumerable: true, get: function () { return interactive_verifier_1.generateQualityFeedback; } });
// Retrieval Operations
var retrieval_1 = require("./retrieval");
Object.defineProperty(exports, "checkMemory", { enumerable: true, get: function () { return retrieval_1.checkMemory; } });
Object.defineProperty(exports, "getFullIncident", { enumerable: true, get: function () { return retrieval_1.getFullIncident; } });
Object.defineProperty(exports, "searchByTags", { enumerable: true, get: function () { return retrieval_1.searchByTags; } });
Object.defineProperty(exports, "getRecentIncidents", { enumerable: true, get: function () { return retrieval_1.getRecentIncidents; } });
Object.defineProperty(exports, "enhancedSearch", { enumerable: true, get: function () { return retrieval_1.enhancedSearch; } });
// Main Debugging Wrapper
// Note: These will be added when we copy debug-with-memory.ts
var debug_wrapper_1 = require("./debug-wrapper");
Object.defineProperty(exports, "debugWithMemory", { enumerable: true, get: function () { return debug_wrapper_1.debugWithMemory; } });
Object.defineProperty(exports, "storeDebugIncident", { enumerable: true, get: function () { return debug_wrapper_1.storeDebugIncident; } });
Object.defineProperty(exports, "getMemoryStatus", { enumerable: true, get: function () { return debug_wrapper_1.getMemoryStatus; } });
// Pattern Extraction
var pattern_extractor_1 = require("./pattern-extractor");
Object.defineProperty(exports, "extractPatterns", { enumerable: true, get: function () { return pattern_extractor_1.extractPatterns; } });
Object.defineProperty(exports, "suggestPatterns", { enumerable: true, get: function () { return pattern_extractor_1.suggestPatterns; } });
Object.defineProperty(exports, "autoExtractPatternIfReady", { enumerable: true, get: function () { return pattern_extractor_1.autoExtractPatternIfReady; } });
// Batch Operations
var batch_operations_1 = require("./batch-operations");
Object.defineProperty(exports, "batchReviewIncomplete", { enumerable: true, get: function () { return batch_operations_1.batchReviewIncomplete; } });
Object.defineProperty(exports, "batchExtractPatterns", { enumerable: true, get: function () { return batch_operations_1.batchExtractPatterns; } });
Object.defineProperty(exports, "batchCleanup", { enumerable: true, get: function () { return batch_operations_1.batchCleanup; } });
// Audit Trail Mining
var audit_miner_1 = require("./audit-miner");
Object.defineProperty(exports, "mineAuditTrail", { enumerable: true, get: function () { return audit_miner_1.mineAuditTrail; } });
Object.defineProperty(exports, "previewAuditMining", { enumerable: true, get: function () { return audit_miner_1.previewAuditMining; } });
// Token-Efficient Retrieval
var retrieval_2 = require("./retrieval");
Object.defineProperty(exports, "checkMemoryTiered", { enumerable: true, get: function () { return retrieval_2.checkMemoryTiered; } });
Object.defineProperty(exports, "quickMemoryCheck", { enumerable: true, get: function () { return retrieval_2.quickMemoryCheck; } });
Object.defineProperty(exports, "getIncidentDetails", { enumerable: true, get: function () { return retrieval_2.getIncidentDetails; } });
Object.defineProperty(exports, "getPatternDetails", { enumerable: true, get: function () { return retrieval_2.getPatternDetails; } });
// v1.5.0 verdict system
Object.defineProperty(exports, "checkMemoryWithVerdict", { enumerable: true, get: function () { return retrieval_2.checkMemoryWithVerdict; } });
Object.defineProperty(exports, "classifyVerdict", { enumerable: true, get: function () { return retrieval_2.classifyVerdict; } });
// v1.6.0 progressive + scaled
Object.defineProperty(exports, "checkMemoryProgressive", { enumerable: true, get: function () { return retrieval_2.checkMemoryProgressive; } });
Object.defineProperty(exports, "checkMemoryScaled", { enumerable: true, get: function () { return retrieval_2.checkMemoryScaled; } });
// Parallel Retrieval
var parallel_retrieval_1 = require("./parallel-retrieval");
Object.defineProperty(exports, "parallelSearch", { enumerable: true, get: function () { return parallel_retrieval_1.parallelSearch; } });
Object.defineProperty(exports, "parallelPatternMatch", { enumerable: true, get: function () { return parallel_retrieval_1.parallelPatternMatch; } });
Object.defineProperty(exports, "parallelMemoryCheck", { enumerable: true, get: function () { return parallel_retrieval_1.parallelMemoryCheck; } });
// Assessment Orchestration
var assessment_orchestrator_1 = require("./assessment-orchestrator");
Object.defineProperty(exports, "detectDomains", { enumerable: true, get: function () { return assessment_orchestrator_1.detectDomains; } });
Object.defineProperty(exports, "selectDomainsForAssessment", { enumerable: true, get: function () { return assessment_orchestrator_1.selectDomainsForAssessment; } });
Object.defineProperty(exports, "generateAssessorPrompts", { enumerable: true, get: function () { return assessment_orchestrator_1.generateAssessorPrompts; } });
Object.defineProperty(exports, "parseAssessmentResponse", { enumerable: true, get: function () { return assessment_orchestrator_1.parseAssessmentResponse; } });
Object.defineProperty(exports, "rankAssessments", { enumerable: true, get: function () { return assessment_orchestrator_1.rankAssessments; } });
Object.defineProperty(exports, "createOrchestrationResult", { enumerable: true, get: function () { return assessment_orchestrator_1.createOrchestrationResult; } });
Object.defineProperty(exports, "formatOrchestrationResult", { enumerable: true, get: function () { return assessment_orchestrator_1.formatOrchestrationResult; } });
Object.defineProperty(exports, "prepareOrchestration", { enumerable: true, get: function () { return assessment_orchestrator_1.prepareOrchestration; } });
// Result Aggregation
var result_aggregator_1 = require("./result-aggregator");
Object.defineProperty(exports, "scoreAssessment", { enumerable: true, get: function () { return result_aggregator_1.scoreAssessment; } });
Object.defineProperty(exports, "scoreIncident", { enumerable: true, get: function () { return result_aggregator_1.scoreIncident; } });
Object.defineProperty(exports, "scorePattern", { enumerable: true, get: function () { return result_aggregator_1.scorePattern; } });
Object.defineProperty(exports, "aggregateResults", { enumerable: true, get: function () { return result_aggregator_1.aggregateResults; } });
Object.defineProperty(exports, "formatAggregatedResults", { enumerable: true, get: function () { return result_aggregator_1.formatAggregatedResults; } });
Object.defineProperty(exports, "createQuickSummary", { enumerable: true, get: function () { return result_aggregator_1.createQuickSummary; } });
// Context Engine (v1.6.0)
var context_engine_1 = require("./context-engine");
Object.defineProperty(exports, "generateDynamicSection", { enumerable: true, get: function () { return context_engine_1.generateDynamicSection; } });
Object.defineProperty(exports, "generateSessionContext", { enumerable: true, get: function () { return context_engine_1.generateSessionContext; } });
Object.defineProperty(exports, "checkFileContext", { enumerable: true, get: function () { return context_engine_1.checkFileContext; } });
// Trace Ingestion (v1.4.0)
__exportStar(require("./traces"), exports);
// Internal Logger (v1.8.0)
var logger_1 = require("./logger");
Object.defineProperty(exports, "traced", { enumerable: true, get: function () { return logger_1.traced; } });
Object.defineProperty(exports, "log", { enumerable: true, get: function () { return logger_1.log; } });
Object.defineProperty(exports, "readLogs", { enumerable: true, get: function () { return logger_1.readLogs; } });
Object.defineProperty(exports, "configureLogger", { enumerable: true, get: function () { return logger_1.configureLogger; } });
// Log Reader (v1.8.0)
var log_reader_1 = require("./log-reader");
Object.defineProperty(exports, "readProjectLogs", { enumerable: true, get: function () { return log_reader_1.readProjectLogs; } });
//# sourceMappingURL=index.js.map