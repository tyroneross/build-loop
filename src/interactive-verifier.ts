/**
 * Interactive Verification System
 *
 * Guides users through completing incident details with interactive prompts.
 * Improves incident quality by ensuring all critical fields are filled.
 */

import prompts from 'prompts';
import type { Incident, RootCause, Fix, Verification, QualityGates, Completeness } from './types';

/**
 * Build a complete incident using interactive prompts
 */
export async function buildIncidentInteractive(
  baseIncident: Partial<Incident>
): Promise<Incident> {
  console.log('\n📝 Interactive Incident Builder');
  console.log('Let\'s ensure this incident is complete and useful for future reference.\n');

  // Initialize with base incident
  let incident = { ...baseIncident } as Incident;

  // 1. Check and improve root cause
  incident.root_cause = await ensureRootCauseQuality(incident.root_cause);

  // 2. Ensure fix details are complete
  incident.fix = await ensureFixDetails(incident.fix);

  // 3. Verification status
  incident.verification = await ensureVerification(incident.verification);

  // 4. Suggest and collect tags
  incident.tags = await ensureTags(incident.symptom, incident.tags || []);

  // 5. Ensure files changed are documented
  if (!incident.files_changed || incident.files_changed.length === 0) {
    incident.files_changed = await collectFilesChanged();
  }

  // 6. Calculate quality score
  const qualityScore = calculateQualityScore(incident);

  // Update completeness
  incident.completeness = {
    symptom: !!incident.symptom && incident.symptom.length >= 20,
    root_cause: !!incident.root_cause?.description && incident.root_cause.description.length >= 50,
    fix: !!incident.fix?.approach && (incident.fix.changes?.length || 0) > 0,
    verification: incident.verification?.status === 'verified',
    quality_score: qualityScore
  };

  // 7. Show final quality report
  console.log('\n📊 Quality Report:');
  console.log(`   Overall Score: ${(qualityScore * 100).toFixed(0)}%`);
  console.log(`   Root Cause: ${incident.completeness.root_cause ? '✅' : '⚠️'}`);
  console.log(`   Fix Details: ${incident.completeness.fix ? '✅' : '⚠️'}`);
  console.log(`   Verification: ${incident.completeness.verification ? '✅' : '⚠️'}`);
  console.log(`   Tags: ${incident.tags.length >= 3 ? '✅' : '⚠️'} (${incident.tags.length})`);

  // 8. Confirm storage
  const { confirm } = await prompts({
    type: 'confirm',
    name: 'confirm',
    message: 'Store this incident?',
    initial: true
  });

  if (!confirm) {
    throw new Error('Incident storage cancelled by user');
  }

  return incident;
}

/**
 * Ensure root cause has sufficient detail (min 50 characters)
 */
async function ensureRootCauseQuality(
  rootCause?: RootCause
): Promise<RootCause> {
  if (!rootCause) {
    const { description } = await prompts({
      type: 'text',
      name: 'description',
      message: 'What was the root cause? (min 50 chars)',
      validate: (value: string) => value.length >= 50 || 'Please provide at least 50 characters'
    });

    return {
      description,
      category: 'unknown',
      confidence: 0.5
    };
  }

  // Check if description is too short
  if (!rootCause.description || rootCause.description.length < 50) {
    console.log(`\n⚠️  Root cause description is too brief (${rootCause.description?.length || 0} chars, need 50+)`);

    const { description } = await prompts({
      type: 'text',
      name: 'description',
      message: 'Please provide a more detailed root cause explanation:',
      initial: rootCause.description || '',
      validate: (value: string) => value.length >= 50 || 'Please provide at least 50 characters'
    });

    rootCause.description = description;
  }

  // Ensure confidence is set
  if (rootCause.confidence === undefined || rootCause.confidence === 0) {
    const { confidence } = await prompts({
      type: 'number',
      name: 'confidence',
      message: 'How confident are you in this root cause? (0-1)',
      initial: 0.8,
      min: 0,
      max: 1,
      float: true
    });

    rootCause.confidence = confidence;
  }

  // Ensure category is set
  if (!rootCause.category || rootCause.category === 'unknown') {
    const { category } = await prompts({
      type: 'select',
      name: 'category',
      message: 'What category best describes this issue?',
      choices: [
        { title: 'Logic Error', value: 'logic' },
        { title: 'Configuration', value: 'config' },
        { title: 'API/Integration', value: 'api' },
        { title: 'Database', value: 'database' },
        { title: 'Dependency', value: 'dependency' },
        { title: 'React/Hooks', value: 'react-hooks' },
        { title: 'TypeScript/Types', value: 'typescript' },
        { title: 'Performance', value: 'performance' },
        { title: 'Security', value: 'security' },
        { title: 'Other', value: 'other' }
      ]
    });

    rootCause.category = category;
  }

  return rootCause;
}

/**
 * Ensure fix details are documented
 */
async function ensureFixDetails(fix?: Fix): Promise<Fix> {
  if (!fix || !fix.approach) {
    const { approach } = await prompts({
      type: 'text',
      name: 'approach',
      message: 'How did you fix this issue? (high-level approach)',
      validate: (value: string) => value.length >= 20 || 'Please provide at least 20 characters'
    });

    return {
      approach,
      changes: []
    };
  }

  // Ensure changes are documented
  if (!fix.changes || fix.changes.length === 0) {
    const { hasChanges } = await prompts({
      type: 'confirm',
      name: 'hasChanges',
      message: 'Would you like to document the specific file changes?',
      initial: true
    });

    if (hasChanges) {
      fix.changes = [];
      let addMore = true;

      while (addMore) {
        const change = await prompts([
          {
            type: 'text',
            name: 'file',
            message: 'File path:'
          },
          {
            type: 'number',
            name: 'lines_changed',
            message: 'Approximate lines changed:',
            initial: 5
          },
          {
            type: 'select',
            name: 'change_type',
            message: 'Change type:',
            choices: [
              { title: 'Modified', value: 'modify' },
              { title: 'Added', value: 'add' },
              { title: 'Deleted', value: 'delete' },
              { title: 'Refactored', value: 'refactor' }
            ]
          },
          {
            type: 'text',
            name: 'summary',
            message: 'What changed:',
            validate: (value: string) => value.length > 0 || 'Summary required'
          }
        ]);

        fix.changes.push(change);

        const { more } = await prompts({
          type: 'confirm',
          name: 'more',
          message: 'Add another file change?',
          initial: false
        });

        addMore = more;
      }
    }
  }

  return fix;
}

/**
 * Ensure verification status is documented
 */
async function ensureVerification(
  verification?: Verification
): Promise<Verification> {
  if (!verification) {
    verification = {
      status: 'unverified',
      regression_tests_passed: false,
      user_journey_tested: false,
      success_criteria_met: false
    };
  }

  const { status } = await prompts({
    type: 'select',
    name: 'status',
    message: 'Has this fix been verified?',
    choices: [
      { title: 'Fully Verified', value: 'verified', description: 'Tested and confirmed working' },
      { title: 'Partially Verified', value: 'partial', description: 'Some testing done' },
      { title: 'Not Verified', value: 'unverified', description: 'No testing yet' }
    ],
    initial: verification.status === 'verified' ? 0 : verification.status === 'partial' ? 1 : 2
  });

  verification.status = status;

  // If verified, ask about specific verification methods
  if (status === 'verified' || status === 'partial') {
    const verificationDetails = await prompts([
      {
        type: 'confirm',
        name: 'regression_tests_passed',
        message: 'Did existing tests pass?',
        initial: verification.regression_tests_passed
      },
      {
        type: 'confirm',
        name: 'user_journey_tested',
        message: 'Did you test the user journey?',
        initial: verification.user_journey_tested
      },
      {
        type: 'confirm',
        name: 'success_criteria_met',
        message: 'Did the fix meet success criteria?',
        initial: verification.success_criteria_met
      }
    ]);

    verification = { ...verification, ...verificationDetails };
  }

  return verification;
}

/**
 * Suggest and collect tags based on symptom analysis
 */
async function ensureTags(symptom: string, existingTags: string[]): Promise<string[]> {
  // Suggest tags based on symptom keywords
  const suggestedTags = suggestTagsFromSymptom(symptom);

  // Combine with existing tags (deduplicate)
  const allTags = [...new Set([...existingTags, ...suggestedTags])];

  console.log(`\n🏷️  Suggested tags based on symptom: ${suggestedTags.join(', ')}`);

  const { tags } = await prompts({
    type: 'list',
    name: 'tags',
    message: 'Tags (comma-separated, min 3 recommended):',
    initial: allTags.join(', '),
    separator: ',',
    format: (val: string) => val.split(',').map((t: string) => t.trim()).filter(Boolean)
  });

  return tags || allTags;
}

/**
 * Suggest tags based on symptom text analysis
 */
function suggestTagsFromSymptom(symptom: string): string[] {
  const tags: string[] = [];
  const lower = symptom.toLowerCase();

  // Technology keywords
  if (lower.includes('react') || lower.includes('component') || lower.includes('hook')) tags.push('react');
  if (lower.includes('typescript') || lower.includes('type error')) tags.push('typescript');
  if (lower.includes('api') || lower.includes('endpoint')) tags.push('api');
  if (lower.includes('database') || lower.includes('query') || lower.includes('sql')) tags.push('database');
  if (lower.includes('cache') || lower.includes('caching')) tags.push('caching');
  if (lower.includes('auth') || lower.includes('authentication')) tags.push('auth');

  // Issue type keywords
  if (lower.includes('error') || lower.includes('crash') || lower.includes('fail')) tags.push('error');
  if (lower.includes('slow') || lower.includes('performance') || lower.includes('timeout')) tags.push('performance');
  if (lower.includes('infinite loop') || lower.includes('hang')) tags.push('infinite-loop');
  if (lower.includes('memory') || lower.includes('leak')) tags.push('memory');
  if (lower.includes('security') || lower.includes('vulnerability')) tags.push('security');

  return tags;
}

/**
 * Collect files that were changed
 */
async function collectFilesChanged(): Promise<string[]> {
  const { hasFiles } = await prompts({
    type: 'confirm',
    name: 'hasFiles',
    message: 'Would you like to document which files were changed?',
    initial: true
  });

  if (!hasFiles) return [];

  const { files } = await prompts({
    type: 'list',
    name: 'files',
    message: 'File paths (comma-separated):',
    separator: ',',
    format: (val: string) => val.split(',').map((f: string) => f.trim()).filter(Boolean)
  });

  return files || [];
}

/**
 * Calculate overall quality score for an incident
 *
 * Scoring rubric:
 * - Root cause analysis: 30% (description length + confidence)
 * - Fix details: 30% (approach + changes documented)
 * - Verification: 20% (verification status)
 * - Documentation: 20% (tags + prevention advice)
 */
export function calculateQualityScore(incident: Incident | Partial<Incident>): number {
  let score = 0.0;

  // Root cause analysis (0.3 max)
  if (incident.root_cause) {
    // Description quality (0.15 max)
    const descLength = incident.root_cause.description?.length || 0;
    if (descLength >= 50) score += 0.10;
    if (descLength >= 100) score += 0.05;

    // Confidence (0.15 max)
    const confidence = incident.root_cause.confidence || 0;
    if (confidence >= 0.7) score += 0.10;
    if (confidence >= 0.9) score += 0.05;
  }

  // Fix details (0.3 max)
  if (incident.fix) {
    // Approach documented (0.15 max)
    if (incident.fix.approach && incident.fix.approach.length >= 20) {
      score += 0.15;
    }

    // Changes documented (0.15 max)
    const changesCount = incident.fix.changes?.length || 0;
    if (changesCount >= 1) score += 0.10;
    if (changesCount >= 3) score += 0.05;
  }

  // Verification (0.2 max)
  if (incident.verification) {
    if (incident.verification.status === 'verified') score += 0.15;
    else if (incident.verification.status === 'partial') score += 0.08;

    // Additional verification details (0.05 max)
    if (incident.verification.regression_tests_passed) score += 0.025;
    if (incident.verification.user_journey_tested) score += 0.025;
  }

  // Documentation (0.2 max)
  const tagsCount = incident.tags?.length || 0;
  if (tagsCount >= 2) score += 0.05;
  if (tagsCount >= 3) score += 0.05;
  if (tagsCount >= 5) score += 0.05;

  // Prevention advice
  if ((incident as any).prevention) score += 0.05;

  return Math.min(score, 1.0); // Cap at 1.0
}

/**
 * Generate quality feedback text
 */
export function generateQualityFeedback(incident: Incident): string {
  const score = calculateQualityScore(incident);
  const percentage = (score * 100).toFixed(0);

  const feedback: string[] = [];
  feedback.push(`Overall Quality: ${percentage}%`);

  if (score >= 0.9) {
    feedback.push('✅ Excellent - This incident is well documented and highly reusable.');
  } else if (score >= 0.75) {
    feedback.push('✅ Good - This incident has sufficient detail for future reference.');
  } else if (score >= 0.5) {
    feedback.push('⚠️  Fair - Consider adding more details to improve reusability.');
  } else {
    feedback.push('❌ Poor - This incident needs more detail to be useful.');
  }

  // Specific improvement suggestions
  const suggestions: string[] = [];

  if (!incident.root_cause?.description || incident.root_cause.description.length < 50) {
    suggestions.push('- Add more detail to root cause analysis');
  }

  if ((incident.root_cause?.confidence || 0) < 0.7) {
    suggestions.push('- Increase confidence score if diagnosis is clear');
  }

  if (!incident.fix?.approach || incident.fix.approach.length < 20) {
    suggestions.push('- Document the fix approach more thoroughly');
  }

  if ((incident.fix?.changes?.length || 0) === 0) {
    suggestions.push('- Document specific file changes');
  }

  if (incident.verification?.status !== 'verified') {
    suggestions.push('- Verify the fix works before storing');
  }

  if ((incident.tags?.length || 0) < 3) {
    suggestions.push('- Add more tags for better categorization');
  }

  if (suggestions.length > 0) {
    feedback.push('\nSuggestions for improvement:');
    feedback.push(...suggestions);
  }

  return feedback.join('\n');
}
