# Prompt: Finalize Phase-2 Intelligence Spec Files

Use this prompt with the four required attachments listed below. It is tuned for the BTC Discipline Bot's phase-2 intelligence layer and is intended to produce finalized `requirements.md`, `design.md`, and `tasks.md` documents from the existing phase-1 specs plus the v2 research notes.

---

## Required Attachments

Attach these four Markdown files to the AI conversation:

1. `requirements.md` - the current phase-1 requirements baseline
2. `design.md` - the current phase-1 technical design baseline
3. `tasks.md` - the current phase-1 implementation task baseline
4. `v2-intelligence-research.md` - the research notes for the phase-2 intelligence layer

If any required attachment is missing, stop immediately and output only:

```markdown
Missing attachments:
- <filename>
- <filename>
```

Do not continue until all four files are present.

---

## Prompt

You are an expert spec-driven systems architect, requirements analyst, and technical design reviewer.

I am attaching four files:

- `requirements.md`
- `design.md`
- `tasks.md`
- `v2-intelligence-research.md`

Your task is to produce finalized phase-2 versions of:

- `requirements.md`
- `design.md`
- `tasks.md`

These documents are for the BTC Discipline Bot's v2 intelligence layer.

The three attached baseline files are the source of truth for existing behavior, constraints, terminology, numbering style, and architectural boundaries. The research file is not yet a spec. Your job is to convert that research into a coherent, implementation-ready phased v2 specification without regressing or weakening the baseline system.

## Primary Goal

Extend the existing BTC Discipline Bot specs so v2 adds an AI-driven, Telegram-integrated market intelligence layer that:

- captures relevant BTC market news and adjacent signals,
- classifies event type and sentiment,
- estimates likely direction, magnitude band, and duration of impact using historical analogs,
- informs the user through Telegram,
- remains strictly read-only with respect to trading discipline enforcement.

## Non-Negotiable Constraints

1. Treat the attached phase-1 `requirements.md`, `design.md`, and `tasks.md` as the baseline source of truth.
2. Preserve all existing v1 functionality, non-goals, acceptance criteria, operational constraints, and implementation intent unless an explicit v2 revision is required.
3. REQ-010's read-only intelligence boundary is binding:
   - the intelligence layer may write to `signals` and publish intelligence-related events;
   - it must not write to trades, breaches, alerts, or conversation state;
   - it must not decide whether a trade is opened, blocked, sized, modified, or closed.
4. The AI agent is an informational subsystem, not a trade-enforcement actor.
5. Telegram intelligence messages must remain clearly distinct from deterministic breach alerts in wording, formatting, and user expectations.
6. The result must remain a single-user, self-hosted, Telegram-first BTC discipline tool unless the attached files explicitly expand scope.
7. The v2 research notes are a menu of ideas, not a list to copy mechanically. Synthesize them into a coherent product and engineering spec.

## Preservation Rules

1. Do not remove, weaken, rename, or silently reinterpret existing v1 requirements unless a v2 revision truly requires it.
2. If a baseline statement remains valid in v2, preserve it.
3. If a baseline statement must change for v2, update it with the smallest safe revision and make the new behavior explicit.
4. Do not let AI-derived signals override deterministic discipline rules.
5. Do not introduce unrelated features such as trade execution, multi-asset expansion, a web UI, or LLM-driven rule enforcement unless the attachments explicitly require them.

## What You Must Resolve in the Finalized Docs

You must make concrete, consistent decisions across `requirements.md`, `design.md`, and `tasks.md` for all of the following areas:

### 1. Intelligence scope and phased rollout

- Define a full phased v2, not just a vague MVP.
- Preserve the difference between launch scope, later phases, and explicit out-of-scope items.
- Make phased boundaries clear enough that implementation tasks can be ordered by dependency.

### 2. Ingestion and source strategy

- Decide which source categories are included at launch versus later phases:
  - aggregator APIs,
  - direct RSS / first-party feeds,
  - macro calendar sources,
  - X/Twitter alternatives,
  - on-chain / derivatives feeds.
- Define deduplication, normalization, source attribution, and source quality handling.
- Define how the system avoids duplicate amplification and noisy low-value signals.

### 3. Classification and sentiment architecture

- Define the classification taxonomy the system will use.
- Define the sentiment and signal output shape.
- Specify whether the system uses a fast-lane / slow-lane architecture, and how escalation works.
- Make the AI-agent role explicit in both analysis and Telegram interaction.

### 4. Historical analog retrieval and impact projection

- Define how the historical analog database is built and used.
- Specify embedding, retrieval, and filtering behavior at the design level.
- Define how expected impact direction, magnitude band, and duration are estimated.
- Make the user-facing duration output explicit.

### 5. Data model and storage

- Define the v2 `signals` payload structure clearly.
- Cover category, direction, magnitude, confidence, horizon, analog references, model versions, and source attribution.
- Define the storage and vector-search choice for launch and the migration boundary relative to the existing Redis seam.

### 6. Telegram UX and command surface

- Replace the v1 `/signals` stub with real v2 behavior.
- Add only justified new intelligence commands.
- Define notification throttling, severity rules, and informational message styling.
- Keep intelligence messaging visibly separate from breach and monitor-health alerts.

### 7. Internal integration and boundaries

- Define event-bus subscriptions and publications required by v2.
- Define how the intelligence layer interacts with existing modules without violating REQ-010.
- Preserve deterministic v1 trade-discipline behavior and operational flows.

### 8. Config, operations, and guardrails

- Define new intelligence env vars, feature flags, provider config, and budget controls.
- Cover prompt-injection defense, citation fidelity, cost tracking, model versioning, and failure handling.
- Cover backtesting, evaluation, and monitoring requirements for the intelligence layer.

## Preferred Defaults When the Research Offers Multiple Valid Options

Use these defaults unless the attached baseline docs clearly require a different choice:

1. Keep Telegram as the only user-facing interface.
2. Keep BTC-only scope.
3. Keep the intelligence layer feature-flagged and isolated from v1 behavior.
4. Use a staged rollout that starts conservative and grows in capability.
5. For launch architecture, prefer:
   - aggregator API plus selected direct RSS and calendar feeds,
   - strong dedupe and source attribution,
   - a two-lane analysis flow: lightweight classifier first, LLM escalation second,
   - Redis-aligned storage for launch rather than introducing an unnecessary new operational stack,
   - a curated historical event backfill for launch rather than requiring massive initial data volume.
6. Preserve the research's core behavioral boundary:
   - v2 makes the user better informed;
   - v1 remains the system that enforces discipline.

## Traceability Rules

1. Every new or revised requirement in `requirements.md` must be represented in `design.md`.
2. Every new or revised requirement must be represented by one or more actionable tasks in `tasks.md`.
3. Every task must map back to a requirement and the relevant design area.
4. Do not include tasks that are not justified by the requirements and design.
5. Do not leave major research topics unaccounted for:
   - either implement them in the phased spec,
   - defer them explicitly to a later phase,
   - or mark them out of scope with a reason.

## Document-Specific Instructions

### `requirements.md`

- Preserve the existing requirements style and numbering conventions as much as practical.
- Add or revise functional and non-functional requirements needed for the full phased v2 intelligence layer.
- Make every requirement explicit, testable, and behaviorally clear.
- Preserve the deterministic discipline-system boundaries.
- Include acceptance criteria for the new intelligence behaviors.

### `design.md`

- Preserve the existing technical style, architecture sections, and terminology where practical.
- Extend the existing extension-seam design into a concrete v2 implementation design.
- Be explicit about components, data flow, event flow, storage boundaries, failure modes, and operational guardrails.
- State decisions clearly rather than leaving multiple competing options unresolved.

### `tasks.md`

- Preserve the existing phase-based, dependency-aware task style where practical.
- Make tasks implementation-ready for an AI coding agent.
- Tasks must be atomic, ordered by dependency, clearly scoped, and testable.
- Each task must include:
  - objective,
  - likely files or modules affected,
  - implementation notes,
  - acceptance criteria,
  - test requirements,
  - requirement references.
- Ensure tasks cover phased rollout, backtesting/evaluation, operational guardrails, and documentation updates.

## Decision Policy

If the research presents several plausible choices, do not leave them all open by default. Choose the safest, most coherent option that best fits:

- the existing phase-1 architecture,
- the single-user self-hosted operating model,
- the REQ-010 read-only boundary,
- the need for phased delivery.

If a conflict exists between the baseline docs and the research notes:

1. preserve the baseline unless a v2 change is clearly justified,
2. make the smallest safe update,
3. document the revised behavior in the affected file,
4. avoid introducing speculative architecture just because it was mentioned in research.

If a small assumption is necessary, make the safest project-consistent assumption and encode it directly in the documents. Avoid pushing avoidable decisions back to the human reviewer.

## Writing Rules

- Use precise, testable wording.
- Avoid vague phrases such as "improve the system", "handle better", "as needed", "make it robust", or "etc."
- Do not write generic AI-product language.
- Preserve the baseline naming, tone, and structural rigor.
- Keep the output in Markdown only.
- Do not mention repo paths. Refer only to the attached filenames.

## Required Output Format

Return exactly four top-level sections in this order and nothing else:

```markdown
## Proposed Change Plan
```

- Summarize the major sections you will update in each file.
- List the most important preserved constraints.
- List any unavoidable assumptions that materially shape the result.

```markdown
# requirements.md
```

- Output the complete finalized contents of `requirements.md`.

```markdown
# design.md
```

- Output the complete finalized contents of `design.md`.

```markdown
# tasks.md
```

- Output the complete finalized contents of `tasks.md`.

Do not add a separate appendix, commentary section, or extra summary after `tasks.md` unless that content is part of one of the three documents themselves.

## Final Quality Checks

Before finalizing, verify internally that:

- all four attachments were used,
- unchanged v1 behavior remains intact unless explicitly revised,
- REQ-010's read-only intelligence boundary is preserved everywhere,
- the intelligence layer never becomes a trade-enforcement system,
- `/signals` has real v2 behavior,
- any new intelligence commands are justified and consistent,
- data and config additions are reflected across all three documents,
- backtesting, evaluation, budget controls, and guardrails are covered,
- every new requirement appears in design and tasks,
- every deferred research idea is clearly labeled as later-phase or out-of-scope,
- the result describes a full phased v2 rather than a loose collection of ideas.

