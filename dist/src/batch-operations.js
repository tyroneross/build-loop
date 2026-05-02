"use strict";
/**
 * Batch Operations
 *
 * Batch commands for maintaining the memory system:
 * - Review incomplete incidents
 * - Extract patterns from existing incidents
 * - Clean up old sessions
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.batchReviewIncomplete = batchReviewIncomplete;
exports.batchExtractPatterns = batchExtractPatterns;
exports.batchCleanup = batchCleanup;
const prompts_1 = __importDefault(require("prompts"));
const promises_1 = __importDefault(require("fs/promises"));
const path_1 = __importDefault(require("path"));
const storage_1 = require("./storage");
const pattern_extractor_1 = require("./pattern-extractor");
const config_1 = require("./config");
const interactive_verifier_1 = require("./interactive-verifier");
/**
 * Review and complete incomplete incidents interactively
 */
async function batchReviewIncomplete(config) {
    console.log('\n🔍 Batch Review: Incomplete Incidents');
    console.log('═══════════════════════════════════════════════\n');
    // Load all incidents
    const incidents = await (0, storage_1.loadAllIncidents)(config);
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
        const validation = (0, storage_1.validateIncident)(incident);
        if (validation.warnings.length > 0) {
            console.log(`\n⚠️  Issues:`);
            validation.warnings.forEach(w => console.log(`   - ${w}`));
        }
        const response = await (0, prompts_1.default)({
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
            const confirm = await (0, prompts_1.default)({
                type: 'confirm',
                name: 'value',
                message: 'Are you sure you want to delete this incident?',
                initial: false
            });
            if (confirm.value) {
                await deleteIncident(incident.incident_id, config);
                deleted++;
                console.log('✅ Incident deleted');
            }
            else {
                skipped++;
            }
            continue;
        }
        if (response.action === 'complete') {
            // Interactive completion
            const updated = await completeIncidentInteractive(incident);
            if (updated) {
                await (0, storage_1.storeIncident)(updated, { config, validate_schema: true });
                completed++;
                console.log('✅ Incident updated and saved');
            }
            else {
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
async function batchExtractPatterns(options) {
    const { category, minIncidents = 3, config } = options || {};
    console.log('\n🔍 Batch Pattern Extraction');
    console.log('═══════════════════════════════════════════════\n');
    if (category) {
        console.log(`   Category filter: ${category}`);
    }
    console.log(`   Minimum incidents: ${minIncidents}\n`);
    // Extract patterns
    const patterns = await (0, pattern_extractor_1.extractPatterns)({
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
async function batchCleanup(options) {
    const { olderThanDays = 90, dryRun = false, config } = options || {};
    console.log('\n🧹 Batch Cleanup');
    console.log('═══════════════════════════════════════════════\n');
    if (dryRun) {
        console.log('🔍 DRY RUN MODE - No files will be deleted\n');
    }
    const paths = (0, config_1.getMemoryPaths)(config);
    const cutoffTime = Date.now() - olderThanDays * 24 * 60 * 60 * 1000;
    const cutoffDate = new Date(cutoffTime);
    console.log(`Looking for sessions older than ${cutoffDate.toLocaleDateString()}...\n`);
    // Clean up sessions
    const sessionsDir = paths.sessions;
    let sessionsDeleted = 0;
    try {
        const sessionFiles = await promises_1.default.readdir(sessionsDir);
        for (const file of sessionFiles) {
            if (!file.endsWith('.json'))
                continue;
            const filepath = path_1.default.join(sessionsDir, file);
            const stats = await promises_1.default.stat(filepath);
            if (stats.mtimeMs < cutoffTime) {
                console.log(`   Old session: ${file} (${stats.mtime.toLocaleDateString()})`);
                if (!dryRun) {
                    await promises_1.default.unlink(filepath);
                    sessionsDeleted++;
                }
            }
        }
    }
    catch (error) {
        if (error.code !== 'ENOENT') {
            console.error('Error cleaning sessions:', error);
        }
    }
    // Find incidents with quality issues
    const incidents = await (0, storage_1.loadAllIncidents)(config);
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
        const confirm = await (0, prompts_1.default)({
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
async function completeIncidentInteractive(incident) {
    console.log('\n📝 Complete Incident Interactively\n');
    // Check what's missing
    const needsRootCause = !incident.root_cause.description || incident.root_cause.confidence < 0.5;
    const needsFix = !incident.fix.approach || incident.fix.changes.length === 0;
    const needsVerification = incident.verification.status === 'unverified';
    if (needsRootCause) {
        const rootCauseResponse = await (0, prompts_1.default)([
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
        const fixResponse = await (0, prompts_1.default)([
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
                const files = fixResponse.files.split(',').map((f) => f.trim()).filter(Boolean);
                incident.files_changed = files;
                incident.fix.changes = files.map((file) => ({
                    file,
                    lines_changed: 10,
                    change_type: 'modify',
                    summary: 'Updated'
                }));
            }
        }
    }
    if (needsVerification) {
        const verifyResponse = await (0, prompts_1.default)({
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
        quality_score: (0, interactive_verifier_1.calculateQualityScore)(incident)
    };
    return incident;
}
/**
 * Delete an incident file
 */
async function deleteIncident(incident_id, config) {
    const paths = (0, config_1.getMemoryPaths)(config);
    const filepath = path_1.default.join(paths.incidents, `${incident_id}.json`);
    try {
        await promises_1.default.unlink(filepath);
    }
    catch (error) {
        console.error(`Failed to delete ${incident_id}:`, error);
    }
}
//# sourceMappingURL=batch-operations.js.map