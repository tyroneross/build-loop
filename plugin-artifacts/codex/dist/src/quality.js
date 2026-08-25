"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
Object.defineProperty(exports, "__esModule", { value: true });
exports.calculateQualityScore = calculateQualityScore;
exports.generateQualityFeedback = generateQualityFeedback;
/** Shared incident quality scoring, synced from Coding Debugger v1.9.0. */
function calculateQualityScore(incident) {
    let score = 0.0;
    if (incident.root_cause) {
        const descLength = incident.root_cause.description?.length || 0;
        if (descLength >= 50)
            score += 0.10;
        if (descLength >= 100)
            score += 0.05;
        const confidence = incident.root_cause.confidence || 0;
        if (confidence >= 0.7)
            score += 0.10;
        if (confidence >= 0.9)
            score += 0.05;
    }
    if (incident.fix) {
        if (incident.fix.approach && incident.fix.approach.length >= 20)
            score += 0.15;
        const changesCount = incident.fix.changes?.length || 0;
        if (changesCount >= 1)
            score += 0.10;
        if (changesCount >= 3)
            score += 0.05;
    }
    if (incident.verification) {
        if (incident.verification.status === 'verified')
            score += 0.15;
        else if (incident.verification.status === 'partial')
            score += 0.08;
        if (incident.verification.regression_tests_passed)
            score += 0.025;
        if (incident.verification.user_journey_tested)
            score += 0.025;
    }
    const tagsCount = incident.tags?.length || 0;
    if (tagsCount >= 2)
        score += 0.05;
    if (tagsCount >= 3)
        score += 0.05;
    if (tagsCount >= 5)
        score += 0.05;
    if (incident.prevention)
        score += 0.05;
    return Math.min(score, 1.0);
}
function generateQualityFeedback(incident) {
    const score = calculateQualityScore(incident);
    const percentage = (score * 100).toFixed(0);
    const feedback = [`Overall Quality: ${percentage}%`];
    if (score >= 0.9)
        feedback.push('Excellent - This incident is well documented and highly reusable.');
    else if (score >= 0.75)
        feedback.push('Good - This incident has sufficient detail for future reference.');
    else if (score >= 0.5)
        feedback.push('Fair - Consider adding more details to improve reusability.');
    else
        feedback.push('Poor - This incident needs more detail to be useful.');
    const suggestions = [];
    if (!incident.root_cause?.description || incident.root_cause.description.length < 50)
        suggestions.push('- Add more detail to root cause analysis');
    if ((incident.root_cause?.confidence || 0) < 0.7)
        suggestions.push('- Increase confidence score if diagnosis is clear');
    if (!incident.fix?.approach || incident.fix.approach.length < 20)
        suggestions.push('- Document the fix approach more thoroughly');
    if ((incident.fix?.changes?.length || 0) === 0)
        suggestions.push('- Document specific file changes');
    if (incident.verification?.status !== 'verified')
        suggestions.push('- Verify the fix works before storing');
    if ((incident.tags?.length || 0) < 3)
        suggestions.push('- Add more tags for better categorization');
    if (suggestions.length > 0)
        feedback.push('\nSuggestions for improvement:', ...suggestions);
    return feedback.join('\n');
}
//# sourceMappingURL=quality.js.map