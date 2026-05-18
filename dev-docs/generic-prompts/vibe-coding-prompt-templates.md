# Vibe-Coding Prompt Templates



---

# Step 1 — Mini-Spec Prompt Template

```md
You are helping me define a focused mini-spec before any implementation.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Repository/context: {{REPO_OR_PROJECT_CONTEXT}}
- Target environment: {{TARGET_ENVIRONMENT}}

Feature / change:
- Name: {{FEATURE_NAME}}
- Type: {{FEATURE_TYPE}} <!-- feature | bug fix | refactor | performance | security | docs | test coverage -->
- User story: As a {{USER_TYPE}}, I want {{USER_GOAL}}, so that {{USER_BENEFIT}}.
- Business/product goal: {{BUSINESS_GOAL}}

Current problem:
{{CURRENT_PROBLEM_DESCRIPTION}}

Desired behavior:
{{DESIRED_BEHAVIOR}}

Non-goals:
{{NON_GOALS}}

Constraints:
- Must use: {{REQUIRED_TECH_OR_PATTERNS}}
- Must avoid: {{FORBIDDEN_TECH_OR_PATTERNS}}
- Performance constraints: {{PERFORMANCE_CONSTRAINTS}}
- Security/privacy constraints: {{SECURITY_PRIVACY_CONSTRAINTS}}
- Accessibility constraints: {{ACCESSIBILITY_CONSTRAINTS}}
- Compatibility constraints: {{COMPATIBILITY_CONSTRAINTS}}

Files/directories allowed to edit:
{{FILES_ALLOWED_TO_EDIT}}

Files/directories off-limits:
{{FILES_OFF_LIMITS}}

Edge cases:
{{EDGE_CASES}}

Acceptance criteria:
1. {{ACCEPTANCE_CRITERION_1}}
2. {{ACCEPTANCE_CRITERION_2}}
3. {{ACCEPTANCE_CRITERION_3}}

Testing expectations:
- Unit tests: {{UNIT_TEST_EXPECTATIONS}}
- Integration tests: {{INTEGRATION_TEST_EXPECTATIONS}}
- Manual QA steps: {{MANUAL_QA_STEPS}}

Output format:
Return a concise mini-spec with these sections:
1. Summary
2. Goals
3. Non-goals
4. Requirements
5. Edge cases
6. Acceptance criteria
7. Test plan
8. Risks and assumptions

Do not write code yet.
Ask clarifying questions only if required to prevent a wrong implementation.
```


---

# Step 2 — Inspect First Prompt Template

```md
You are working in an existing codebase. Your first task is to inspect and understand the relevant architecture.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Feature/change: {{FEATURE_NAME}}

Context:
{{MINI_SPEC_OR_ISSUE_LINK}}

Relevant files/directories to inspect:
{{FILES_OR_DIRECTORIES_TO_INSPECT}}

Files/directories that may be relevant but should not be changed yet:
{{FILES_TO_READ_ONLY}}

Questions to answer:
1. Where is the current behavior implemented?
2. What data flow, API flow, or component flow is involved?
3. What existing patterns should the implementation follow?
4. What tests already cover this area?
5. What risks, hidden dependencies, or migration concerns exist?
6. Which files will likely need changes?

Instructions:
- Inspect the codebase first.
- Do not modify files.
- Do not write implementation code.
- Do not install dependencies.
- Do not run destructive commands.
- If information is missing, state the assumption instead of guessing.

Output format:
1. Current architecture summary
2. Relevant files and responsibilities
3. Existing patterns to follow
4. Potential implementation locations
5. Existing test coverage
6. Risks / unknowns
7. Recommended next step
```


---

# Step 3 — Implementation Plan Prompt Template

```md
Create an implementation plan for the following change.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Feature/change: {{FEATURE_NAME}}

Mini-spec:
{{MINI_SPEC}}

Inspection summary:
{{INSPECTION_SUMMARY}}

Scope rules:
- Allowed files/directories: {{FILES_ALLOWED_TO_EDIT}}
- Off-limits files/directories: {{FILES_OFF_LIMITS}}
- Maximum diff size: {{MAX_DIFF_SIZE_OR_GUIDELINE}}
- Dependencies allowed: {{DEPENDENCY_POLICY}} <!-- none | only existing | ask first | allowed with justification -->

Quality gates:
- Lint command: {{LINT_COMMAND}}
- Typecheck command: {{TYPECHECK_COMMAND}}
- Unit test command: {{UNIT_TEST_COMMAND}}
- Integration test command: {{INTEGRATION_TEST_COMMAND}}
- Build command: {{BUILD_COMMAND}}

Planning instructions:
- Break the work into small, reviewable steps.
- Identify exact files likely to change.
- Include test updates.
- Include rollback or feature-flag strategy if relevant.
- Do not implement yet.
- Ask before expanding scope.

Output format:
1. Implementation strategy
2. Step-by-step plan
3. Files to change
4. Tests to add/update
5. Commands to run
6. Risks and mitigations
7. Open questions
8. Definition of done
```


---

# Step 4 — Generate Small Diff Prompt Template

```md
Implement only the next small step from the approved plan.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Feature/change: {{FEATURE_NAME}}

Approved plan:
{{APPROVED_PLAN}}

This implementation step:
{{CURRENT_STEP_TO_IMPLEMENT}}

Strict scope:
- Only edit these files/directories: {{FILES_ALLOWED_FOR_THIS_STEP}}
- Do not edit: {{FILES_OFF_LIMITS_FOR_THIS_STEP}}
- Do not change public APIs unless explicitly required.
- Do not introduce new dependencies unless explicitly approved.
- Keep the diff small and reviewable.

Coding standards:
- Follow existing project patterns.
- Prefer simple, readable code.
- Preserve backward compatibility where possible.
- Add comments only where they clarify non-obvious behavior.
- Avoid broad refactors unrelated to this step.

Testing:
- Add/update tests for: {{TESTS_TO_ADD_OR_UPDATE}}
- Do not remove tests unless justified.
- Preserve existing behavior not mentioned in the spec.

Output format:
1. Brief summary of changes
2. Files changed
3. Diff or patch
4. Tests added/updated
5. Commands I should run
6. Any risks or follow-up steps

Proceed with implementation for this step only.
```


---

# Step 5 — Review Diff Prompt Template

````md
Review the following diff as a senior engineer.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Feature/change: {{FEATURE_NAME}}

Original requirements:
{{MINI_SPEC_OR_ACCEPTANCE_CRITERIA}}

Approved plan:
{{APPROVED_PLAN}}

Diff to review:
```diff
{{DIFF}}
```

Review priorities:
- Correctness
- Security and privacy
- Auth/permissions
- Data migrations and data integrity
- API compatibility
- Error handling
- Race conditions / concurrency
- Performance
- Accessibility
- Test coverage
- Maintainability
- Unintended changes
- Deleted or modified behavior outside scope

Instructions:
- Be critical and specific.
- Identify blockers separately from suggestions.
- Cite exact files/functions/lines when possible.
- Do not rewrite the whole solution unless necessary.
- Recommend the smallest safe fix for each issue.

Output format:
## Verdict
{{APPROVE_OR_REQUEST_CHANGES}}

## Blockers
- {{BLOCKER_1}}

## Non-blocking suggestions
- {{SUGGESTION_1}}

## Security/privacy concerns
- {{SECURITY_CONCERN_1}}

## Test gaps
- {{TEST_GAP_1}}

## Minimal fix plan
1. {{FIX_STEP_1}}

## Final checklist
- [ ] Requirements satisfied
- [ ] Scope respected
- [ ] Tests adequate
- [ ] No obvious security regressions
- [ ] Ready for commit
````


---

# Step 6 — Run Checks Prompt Template

```md
Help me run and interpret quality checks for this change.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Feature/change: {{FEATURE_NAME}}

Changed files:
{{CHANGED_FILES}}

Commands available:
- Install command: {{INSTALL_COMMAND}}
- Format command: {{FORMAT_COMMAND}}
- Lint command: {{LINT_COMMAND}}
- Typecheck command: {{TYPECHECK_COMMAND}}
- Unit test command: {{UNIT_TEST_COMMAND}}
- Integration test command: {{INTEGRATION_TEST_COMMAND}}
- Build command: {{BUILD_COMMAND}}
- E2E command: {{E2E_COMMAND}}

Instructions:
1. Recommend the correct order to run checks.
2. Explain what each command validates.
3. If I paste failures, diagnose the root cause.
4. Propose the smallest safe fix.
5. Do not mask failures by deleting tests or weakening assertions.
6. Do not suggest bypassing lint/typecheck/build unless this is explicitly a temporary emergency workaround.

Output format:
## Recommended check order
1. {{CHECK_1}}
2. {{CHECK_2}}
3. {{CHECK_3}}

## Expected pass criteria
- {{PASS_CRITERION_1}}

## Failure triage guide
- If {{FAILURE_TYPE}}, check {{LIKELY_CAUSE}}

## Fix rules
- Preserve intended behavior.
- Fix root cause, not symptoms.
- Keep changes scoped.
```


---

# Step 7 — Commit Prompt Template

````md
Create a clean git commit message for this change.

Project:
- Name: {{PROJECT_NAME}}
- Feature/change: {{FEATURE_NAME}}

Summary of changes:
{{SUMMARY_OF_CHANGES}}

Files changed:
{{CHANGED_FILES}}

Tests/checks run:
{{CHECKS_RUN_AND_RESULTS}}

Issue/ticket:
{{ISSUE_OR_TICKET_ID}}

Commit style:
{{COMMIT_STYLE}} <!-- conventional commits | plain English | project-specific -->

Breaking change?
{{BREAKING_CHANGE_YES_NO}}

User-facing impact:
{{USER_FACING_IMPACT}}

Instructions:
- Use one logical commit.
- Keep the subject line concise.
- Mention tests in the body if relevant.
- Mention breaking changes explicitly.
- Do not exaggerate scope.

Output format:
```txt
{{COMMIT_SUBJECT}}

{{COMMIT_BODY}}

{{FOOTERS_OR_REFERENCES}}
```

Also provide:
1. A short explanation of why this commit message fits the change.
2. A pre-commit checklist.
````


---

# Step 8 — PR Review Prompt Template

````md
Review this pull request as a senior engineer and product-minded reviewer.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- PR title: {{PR_TITLE}}
- PR link or branch: {{PR_LINK_OR_BRANCH}}
- Related issue/ticket: {{ISSUE_OR_TICKET_ID}}

PR description:
{{PR_DESCRIPTION}}

Requirements / acceptance criteria:
{{ACCEPTANCE_CRITERIA}}

Changed files:
{{CHANGED_FILES}}

Diff:
```diff
{{PR_DIFF}}
```

Checks:
- Lint: {{LINT_RESULT}}
- Typecheck: {{TYPECHECK_RESULT}}
- Unit tests: {{UNIT_TEST_RESULT}}
- Integration tests: {{INTEGRATION_TEST_RESULT}}
- Build: {{BUILD_RESULT}}
- E2E: {{E2E_RESULT}}

Review focus:
- Requirements match
- Scope control
- Security/privacy
- Auth/permissions
- Data correctness
- API compatibility
- UI/UX/accessibility
- Performance
- Tests
- Observability/logging
- Rollback plan

Output format:
## PR verdict
{{APPROVE_OR_REQUEST_CHANGES}}

## Summary
{{PR_SUMMARY}}

## Requirement coverage
| Requirement | Covered? | Notes |
|---|---:|---|
| {{REQUIREMENT_1}} | {{YES_NO_PARTIAL}} | {{NOTES}} |

## Blockers
- {{BLOCKER_1}}

## Suggested improvements
- {{SUGGESTION_1}}

## Test gaps
- {{TEST_GAP_1}}

## Security/privacy notes
- {{SECURITY_NOTE_1}}

## Merge readiness checklist
- [ ] Requirements met
- [ ] Checks passing
- [ ] Tests sufficient
- [ ] Rollback path clear
- [ ] Reviewer concerns resolved
````


---

# Step 9 — Deploy Behind a Flag Prompt Template

```md
Create a safe rollout plan for deploying this change behind a feature flag or equivalent guardrail.

Project:
- Name: {{PROJECT_NAME}}
- Stack: {{TECH_STACK}}
- Feature/change: {{FEATURE_NAME}}
- Environment(s): {{ENVIRONMENTS}}
- Release owner: {{RELEASE_OWNER}}

Change summary:
{{SUMMARY_OF_CHANGE}}

Risk level:
{{RISK_LEVEL}} <!-- low | medium | high -->

Feature flag / rollout mechanism:
- Flag name: {{FEATURE_FLAG_NAME}}
- Default state: {{DEFAULT_FLAG_STATE}}
- Target users/groups: {{TARGET_USERS_OR_GROUPS}}
- Rollout percentages: {{ROLLOUT_PERCENTAGES}}
- Kill switch available: {{KILL_SWITCH_YES_NO}}

Dependencies:
{{DEPLOYMENT_DEPENDENCIES}}

Database or migration changes:
{{DATABASE_OR_MIGRATION_CHANGES}}

Monitoring:
- Metrics to watch: {{METRICS_TO_WATCH}}
- Logs to watch: {{LOGS_TO_WATCH}}
- Error tracking: {{ERROR_TRACKING_SYSTEM}}
- Dashboards: {{DASHBOARD_LINKS}}
- Alert thresholds: {{ALERT_THRESHOLDS}}

Rollback:
- Rollback trigger: {{ROLLBACK_TRIGGER}}
- Rollback steps: {{ROLLBACK_STEPS}}
- Data cleanup needed: {{DATA_CLEANUP_STEPS}}

Instructions:
- Assume production safety matters.
- Use staged rollout.
- Include validation after each stage.
- Include explicit rollback criteria.
- Include communication steps for stakeholders.
- Do not recommend full rollout until checks and monitoring pass.

Output format:
## Deployment strategy
{{DEPLOYMENT_STRATEGY}}

## Rollout stages
| Stage | Audience | Percentage | Validation | Rollback trigger |
|---|---:|---:|---|---|
| 1 | {{AUDIENCE_1}} | {{PERCENTAGE_1}} | {{VALIDATION_1}} | {{ROLLBACK_TRIGGER_1}} |

## Pre-deploy checklist
- [ ] {{PRE_DEPLOY_CHECK_1}}

## Post-deploy validation
- [ ] {{POST_DEPLOY_CHECK_1}}

## Monitoring plan
{{MONITORING_PLAN}}

## Rollback plan
{{ROLLBACK_PLAN}}

## Stakeholder communication
{{COMMUNICATION_PLAN}}
```
