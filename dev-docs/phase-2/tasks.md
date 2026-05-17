# Tasks: BTC Discipline Bot

Assumption: phase-1 tasks `TASK-001` through `TASK-032` are complete and merged. Phase-2 work is incremental on top of the existing `src/`, `tests/`, Docker Compose, Redis, and Telegram command surfaces.

Launch scope for phase 2 is defined by `TASK-033` through `TASK-051`. Later-phase expansion work is defined by `TASK-052` through `TASK-054` and remains feature-flagged unless explicitly enabled.

## Phase 2A: Foundations

- [ ] **TASK-033: Intelligence settings and startup guards**
  - Objective: add intelligence feature flags, provider settings, and startup validation without changing v1 behavior when intelligence is disabled.
  - Likely files or modules affected: `src/config.py`, `src/app.py`, `.env.example`, `tests/unit/test_config.py`, `tests/integration/test_app_startup.py`
  - Implementation notes: add `INTELLIGENCE_ENABLED`, per-source enable flags, model-provider settings, analog retrieval settings, notification cooldown settings, and budget caps. Validate mutually-required settings. When intelligence is enabled, startup must check Redis Stack capability and fail clearly if unavailable.
  - Acceptance criteria: valid config loads; missing required intelligence secrets for enabled features fail startup; `INTELLIGENCE_ENABLED=false` keeps launch intelligence workers fully disabled.
  - Test requirements: unit tests for valid and invalid config combinations; integration tests for startup pass/fail with intelligence on and off.
  - Requirement references: `REQ-008`, `REQ-011`, `REQ-019`, `REQ-021`
  - Dependencies: none

- [ ] **TASK-034: Intelligence models and boundary convention checks**
  - Objective: extend the model layer for phase-2 signal processing and strengthen the code-level read-only boundary.
  - Likely files or modules affected: `src/models/signal.py`, `src/models/news_event.py`, `src/models/analog.py`, `src/models/events.py`, `tests/unit/test_intelligence_models.py`, `tests/unit/test_intelligence_boundary.py`
  - Implementation notes: extend `Signal` to the phase-2 payload contract; add `NewsEvent`, `AnalogRecord`, `SourceAttribution`, and budget-state models; add phase-2 event types such as `news_received`, `signal_candidate`, `signal_emitted`, and `signal_suppressed`. Update the boundary convention test so intelligence code may write only signal-owned namespaces and may not write trade, breach, alert, or conversation namespaces.
  - Acceptance criteria: model validation rejects malformed signal payloads; event models serialize cleanly; boundary test fails on any protected-namespace write from intelligence code.
  - Test requirements: unit tests for signal payload round-trip, analog-record validation, and AST or repository-call boundary enforcement.
  - Requirement references: `REQ-010`, `REQ-014`, `REQ-015`, `REQ-016`, `REQ-020`
  - Dependencies: `TASK-033`

- [ ] **TASK-035: Signal repository methods and Redis Stack bootstrap**
  - Objective: implement the repository and migration support needed for signal storage, raw-event storage, and vector-index bootstrap.
  - Likely files or modules affected: `src/db/keyspace.py`, `src/db/migrations.py`, `src/db/repo.py`, `tests/unit/test_keyspace.py`, `tests/integration/test_signal_repo.py`, `tests/integration/test_redis_migrations.py`
  - Implementation notes: add repository methods for raw-event writes, dedupe locks, story-key upserts, signal writes, signal reads for `/signals` and `/why`, source-health state, budget state, analog record storage, and Redis Stack index bootstrap. Keep all key construction inside the repository layer.
  - Acceptance criteria: signal lifecycle methods work against Redis Stack; index bootstrap is idempotent; repository can list active signals and fetch one signal detail without recomputation.
  - Test requirements: integration tests for bootstrap, signal create/read lifecycle, story-key updates, and Redis Stack capability failure.
  - Requirement references: `REQ-011`, `REQ-013`, `REQ-016`, `REQ-019`, `REQ-021`
  - Dependencies: `TASK-034`

## Phase 2B: Source Ingestion

- [ ] **TASK-036: Source adapter abstraction and scheduler**
  - Objective: create the intelligence source adapter interface and the scheduler that runs enabled adapters safely.
  - Likely files or modules affected: `src/intelligence/config.py`, `src/intelligence/scheduler.py`, `src/intelligence/sources/base.py`, `tests/unit/test_intelligence_scheduler.py`
  - Implementation notes: define a common adapter contract for polling, normalization handoff, and health reporting. The scheduler must isolate adapter failures so one broken source does not stop other enabled sources.
  - Acceptance criteria: enabled sources are scheduled at their configured cadence; one failing adapter marks itself unhealthy without crashing the scheduler.
  - Test requirements: unit tests for scheduling cadence, adapter isolation, and health-state transitions.
  - Requirement references: `REQ-012`, `REQ-019`, `REQ-021`
  - Dependencies: `TASK-033`, `TASK-035`

- [ ] **TASK-037: Aggregator and RSS launch adapters**
  - Objective: implement the launch text-source adapters for one aggregator provider and the configured RSS or first-party feeds.
  - Likely files or modules affected: `src/intelligence/sources/aggregator.py`, `src/intelligence/sources/rss.py`, `tests/unit/test_source_aggregator.py`, `tests/unit/test_source_rss.py`, `tests/integration/test_source_text_feeds.py`
  - Implementation notes: implement a CryptoPanic-compatible adapter behind a provider abstraction and RSS polling with conditional fetch support when possible. Normalize source names, URLs, timestamps, and raw text into a common pre-normalization shape.
  - Acceptance criteria: both adapters can ingest fixture payloads and produce normalized raw items with attribution and timestamps.
  - Test requirements: fixture-driven parser tests and one integration test that stores raw events through the repository.
  - Requirement references: `REQ-012`, `REQ-013`, `REQ-021`
  - Dependencies: `TASK-036`

- [ ] **TASK-038: Macro-calendar and Binance-derivatives launch adapters**
  - Objective: implement the structured launch sources for scheduled macro events and Binance funding or open-interest extremes.
  - Likely files or modules affected: `src/intelligence/sources/macro_calendar.py`, `src/intelligence/sources/binance_derivatives.py`, `src/intelligence/config.py`, `tests/unit/test_source_macro_calendar.py`, `tests/unit/test_source_binance_derivatives.py`
  - Implementation notes: macro adapter must emit reminder candidates for `FOMC`, `CPI`, `NFP`, and `PCE`. Derivatives adapter must emit structured candidates only when configured thresholds are crossed so the bot does not spam raw snapshots.
  - Acceptance criteria: macro reminders can be generated without live headlines; derivatives source emits candidates only on threshold crossings.
  - Test requirements: frozen-clock tests for macro reminder windows and unit tests for funding or OI threshold logic.
  - Requirement references: `REQ-012`, `REQ-018`, `REQ-021`
  - Dependencies: `TASK-036`

- [ ] **TASK-039: Normalization, deduplication, and source-quality handling**
  - Objective: convert heterogeneous source output into one normalized event shape and suppress duplicate or low-value stories before classification.
  - Likely files or modules affected: `src/intelligence/ingestion/normalizer.py`, `src/intelligence/ingestion/deduper.py`, `src/intelligence/ingestion/quality.py`, `src/db/repo.py`, `tests/unit/test_normalizer.py`, `tests/unit/test_deduper.py`, `tests/unit/test_source_quality.py`
  - Implementation notes: canonicalize URLs, strip tracking parameters, compute normalized content hashes, assign `story_key`, compute `novelty`, and maintain source-quality counters. Allow later source records to enrich attribution on an existing `story_key` without generating a new push candidate.
  - Acceptance criteria: duplicate stories from aggregator and RSS collapse into one `story_key`; low-quality items are filtered deterministically; attribution can merge without duplicate notification.
  - Test requirements: unit tests for URL dedupe, content-hash dedupe, `story_key` merging, and source-quality suppression.
  - Requirement references: `REQ-013`, `REQ-016`, `REQ-021`
  - Dependencies: `TASK-035`, `TASK-037`, `TASK-038`

## Phase 2C: Analysis and Projection

- [ ] **TASK-040: Fast-lane classification and structured-source mapping**
  - Objective: classify every eligible normalized event into the required taxonomy and output shape before any slow-lane escalation.
  - Likely files or modules affected: `src/intelligence/classification/fastlane.py`, `src/intelligence/config.py`, `tests/unit/test_fastlane.py`
  - Implementation notes: wrap the local launch model for text-bearing sources, add deterministic classification for macro reminders and derivatives signals, and enforce configurable thresholds for BTC specificity, novelty, and source quality. Output preliminary category, direction, confidence, and magnitude inputs.
  - Acceptance criteria: every eligible candidate receives a taxonomy label and the required fast-lane fields; ineligible candidates are rejected before slow-lane analysis.
  - Test requirements: fixture-driven tests for each taxonomy bucket plus structured-source mapping tests.
  - Requirement references: `REQ-014`, `REQ-018`, `REQ-019`
  - Dependencies: `TASK-039`

- [ ] **TASK-041: Curated analog dataset loader and embeddings client**
  - Objective: bootstrap the launch historical analog set and the embedding client needed for similarity search.
  - Likely files or modules affected: `src/intelligence/retrieval/embeddings.py`, `src/intelligence/retrieval/vector_repo.py`, `scripts/load_curated_analogs.py`, `data/intelligence/curated_analogs.*`, `tests/unit/test_embeddings_client.py`, `tests/integration/test_vector_repo.py`
  - Implementation notes: define the curated analog file format, load analog rows into Redis Stack, persist forward-return windows, and version the embedding model. Keep the embeddings client behind an interface so provider changes do not affect the rest of the pipeline.
  - Acceptance criteria: curated analog fixtures load into Redis Stack with vector docs and metadata; embedding-client configuration is explicit and versioned.
  - Test requirements: integration tests for analog load, search-index document creation, and embedding-client error handling.
  - Requirement references: `REQ-015`, `REQ-016`, `REQ-019`, `REQ-020`
  - Dependencies: `TASK-035`

- [ ] **TASK-042: Analog search and deterministic impact projection**
  - Objective: retrieve top-K analogs and deterministically project direction, magnitude band, and duration band.
  - Likely files or modules affected: `src/intelligence/retrieval/analog_search.py`, `src/intelligence/impact/projection.py`, `tests/unit/test_analog_search.py`, `tests/unit/test_projection.py`
  - Implementation notes: search by category-filtered similarity, return the configured top-K analogs, and compute direction from the median 24-hour return. Implement the magnitude-band and duration-band mapping exactly as specified in the design.
  - Acceptance criteria: projection output is deterministic and matches the documented thresholds; every candidate signal has analog IDs and summary statistics before slow-lane refinement.
  - Test requirements: unit tests for similarity ordering, magnitude thresholds, and hours/days/weeks duration mapping.
  - Requirement references: `REQ-015`, `REQ-016`
  - Dependencies: `TASK-041`

- [ ] **TASK-043: Slow-lane LLM analysis and policy guardrails**
  - Objective: add bounded slow-lane analysis that can refine summaries and structured fields without violating citation or no-advice rules.
  - Likely files or modules affected: `src/intelligence/analysis/llm.py`, `src/intelligence/analysis/policy.py`, `src/intelligence/config.py`, `tests/unit/test_llm_analysis.py`, `tests/unit/test_intelligence_policy.py`
  - Implementation notes: implement structured JSON output validation, prompt-injection defense, citation restrictions to retrieved analog IDs, and a policy filter that rejects prescriptive trade language. Respect budget-circuit and hourly-cap checks.
  - Acceptance criteria: invalid or policy-violating outputs are retried once and then downgraded or dropped; slow-lane analysis never invents analog citations.
  - Test requirements: unit tests for parse failure, injection-like source text, citation validation, and no-trade-advice enforcement.
  - Requirement references: `REQ-014`, `REQ-018`, `REQ-019`, `REQ-020`, `REQ-021`
  - Dependencies: `TASK-040`, `TASK-042`

## Phase 2D: Signal Lifecycle and Telegram UX

- [ ] **TASK-044: Intelligence orchestrator and signal-emission pipeline**
  - Objective: wire launch sources, normalization, classification, retrieval, and persistence into one end-to-end intelligence pipeline.
  - Likely files or modules affected: `src/intelligence/orchestrator.py`, `src/events/bus.py`, `src/models/events.py`, `src/db/repo.py`, `tests/integration/test_intelligence_pipeline.py`
  - Implementation notes: subscribe to the needed event-bus topics, run normalized items through the fast lane, analog retrieval, optional slow lane, then persist one final signal record and publish `signal_emitted` or `signal_suppressed`. Ensure the pipeline reads open-trade context but never writes protected trade-state keys.
  - Acceptance criteria: a launch-source event can flow from adapter output to stored signal; signal-emission events publish on the bus; protected namespaces remain untouched.
  - Test requirements: integration test that feeds a known event fixture through the full pipeline and asserts one stored signal plus one published event.
  - Requirement references: `REQ-010`, `REQ-012`, `REQ-013`, `REQ-014`, `REQ-015`, `REQ-016`, `REQ-021`
  - Dependencies: `TASK-037`, `TASK-038`, `TASK-039`, `TASK-040`, `TASK-042`, `TASK-043`

- [ ] **TASK-045: Severity assignment, throttling, and notifier behavior**
  - Objective: implement the exposure-aware intelligence notification policy and keep it visibly separate from breach alerts.
  - Likely files or modules affected: `src/intelligence/emitters/notifier.py`, `src/bot/formatting.py`, `src/db/repo.py`, `tests/unit/test_intelligence_notifier.py`
  - Implementation notes: assign `low`, `medium`, `high`, and `critical` severity deterministically; enforce one push per `story_key`; implement category cooldown; use current open-trade count to decide whether `high` or `medium` signals push immediately. Macro reminders with open trades are the only medium-severity launch exception.
  - Acceptance criteria: duplicate stories do not send duplicate pushes; push or suppress decisions match the documented policy under a frozen clock.
  - Test requirements: frozen-clock tests for story-level suppression, category cooldown, open-trade-aware high severity, and macro-reminder exceptions.
  - Requirement references: `REQ-017`, `REQ-018`, `REQ-021`
  - Dependencies: `TASK-044`

- [ ] **TASK-046: Replace the `/signals` stub with live signal summaries**
  - Objective: turn `/signals` into a real read-only query over stored intelligence signals.
  - Likely files or modules affected: `src/bot/handlers.py`, `src/bot/formatting.py`, `src/db/repo.py`, `tests/integration/test_handlers.py`
  - Implementation notes: add limit parsing, disabled state, empty state, degraded-signal labeling, summary rendering, and the informational-only footer. Keep the command read-only and fast by using persisted data only.
  - Acceptance criteria: `/signals` returns active or recent signals with severity, category, direction, magnitude, horizon, confidence, and source attribution; disabled intelligence returns a clear message.
  - Test requirements: handler tests for disabled, empty, happy-path, and degraded states.
  - Requirement references: `REQ-006`, `REQ-016`, `REQ-017`
  - Dependencies: `TASK-035`, `TASK-045`

- [ ] **TASK-047: Implement `/why <signal_id>` detail rendering**
  - Objective: give the user an auditable explanation view for one stored signal.
  - Likely files or modules affected: `src/bot/handlers.py`, `src/bot/formatting.py`, `src/db/repo.py`, `tests/integration/test_handlers.py`, `tests/unit/test_signal_formatting.py`
  - Implementation notes: fetch one signal detail, render the analog IDs and summaries, show primary source URL and attribution, and include model-version metadata plus the informational-only footer. Missing or expired signals must return a clear user-facing message.
  - Acceptance criteria: `/why` output always contains at least one attribution reference for source-driven signals and never includes prescriptive trade advice.
  - Test requirements: handler tests for happy-path, missing-signal, and degraded-signal detail rendering.
  - Requirement references: `REQ-006`, `REQ-015`, `REQ-017`, `REQ-020`
  - Dependencies: `TASK-046`

- [ ] **TASK-048: Intelligence health, budget, and degraded-mode observability**
  - Objective: expose intelligence runtime state through `/health` and durable counters.
  - Likely files or modules affected: `src/intelligence/health.py`, `src/db/repo.py`, `src/bot/handlers.py`, `tests/unit/test_intelligence_health.py`, `tests/integration/test_health_intelligence.py`
  - Implementation notes: track source freshness, last signal time, slow-lane budget usage, circuit-breaker state, and degraded mode. `/health` must show intelligence disabled/offline states without masking monitor-health status from v1.
  - Acceptance criteria: `/health` shows enabled state, source freshness, last successful signal time, budget circuit, and degraded-mode status within one polling interval.
  - Test requirements: integration tests for healthy, disabled, source-failed, and budget-exhausted states.
  - Requirement references: `REQ-011`, `REQ-019`, `REQ-020`, `REQ-021`
  - Dependencies: `TASK-033`, `TASK-035`, `TASK-036`, `TASK-044`, `TASK-045`

## Phase 2E: Validation and Documentation

- [ ] **TASK-049: Historical replay and backtest harness**
  - Objective: build the evaluation harness that replays held-out events without look-ahead and produces quality metrics for launch decisions.
  - Likely files or modules affected: `src/intelligence/backtest/replay.py`, `scripts/run_intelligence_replay.py`, `tests/integration/test_replay_harness.py`, `tests/fixtures/intelligence_replay/*`
  - Implementation notes: replay normalized historical events in timestamp order, freeze model versions, retrieve only analogs that precede the replayed event, and compute direction accuracy, confidence calibration, and horizon-band accuracy.
  - Acceptance criteria: the replay harness produces a structured report from a held-out fixture set and fails if look-ahead leakage is detected.
  - Test requirements: integration tests for no-look-ahead enforcement and metric generation on a deterministic fixture.
  - Requirement references: `REQ-015`, `REQ-020`
  - Dependencies: `TASK-041`, `TASK-042`, `TASK-044`

- [ ] **TASK-050: Runtime, deployment, and operational-documentation updates**
  - Objective: update the deployment surface for Redis Stack and document how to operate intelligence safely.
  - Likely files or modules affected: `docker-compose.yml`, `Dockerfile`, `.env.example`, `README.md`, `RUNBOOK.md`
  - Implementation notes: switch Compose to a Redis Stack-capable image or equivalent module loading, document the intelligence feature flags, source toggles, budget controls, `/signals`, `/why`, degraded mode, and rollback path (`INTELLIGENCE_ENABLED=false`).
  - Acceptance criteria: the documented startup path works with intelligence disabled and with intelligence enabled; runbook covers source outages, budget-circuit behavior, and Redis Stack capability checks.
  - Test requirements: one manual verification checklist plus automated startup coverage in integration tests where feasible.
  - Requirement references: `REQ-011`, `REQ-019`, `REQ-021`
  - Dependencies: `TASK-033`, `TASK-035`, `TASK-048`

- [ ] **TASK-051: Launch end-to-end verification**
  - Objective: verify the complete launch slice of phase 2 under realistic event flows and failure modes.
  - Likely files or modules affected: `tests/e2e/test_intelligence_launch.py`, `tests/e2e/test_flows.py`, `tests/fixtures/intelligence_e2e/*`
  - Implementation notes: cover event ingestion, dedupe, analog retrieval, notifier suppression, `/signals`, `/why`, source degradation, and budget exhaustion. Keep the existing v1 discipline E2E flows intact in the same suite.
  - Acceptance criteria: all launch scenarios pass in CI without regressing v1 breach-monitor behavior or Telegram command behavior.
  - Test requirements: E2E scenarios for duplicate article suppression, macro reminders with and without open trades, slow-lane failure fallback, `/why` citation detail, and budget-circuit behavior.
  - Requirement references: `REQ-006`, `REQ-010`, `REQ-012`, `REQ-013`, `REQ-014`, `REQ-015`, `REQ-016`, `REQ-017`, `REQ-018`, `REQ-019`, `REQ-020`, `REQ-021`
  - Dependencies: `TASK-045`, `TASK-046`, `TASK-047`, `TASK-048`, `TASK-049`, `TASK-050`

## Phase 2F: Later-Phase Expansion

- [ ] **TASK-052: Curated X/Twitter-alternative source adapter**
  - Objective: add the deferred social-feed source behind a dedicated feature flag and curated account list.
  - Likely files or modules affected: `src/intelligence/sources/x_stream.py`, `src/intelligence/config.py`, `tests/unit/test_source_x_stream.py`, `tests/integration/test_source_x_stream.py`
  - Implementation notes: ingest a curated account or handle set only; do not add a firehose. Reuse normalization, dedupe, and quality handling from launch. Keep the adapter optional and disabled by default.
  - Acceptance criteria: curated social events flow into the same pipeline without duplicate notification storms; disabling the feature removes all runtime effect.
  - Test requirements: fixture-driven adapter tests and one integration test showing dedupe against an overlapping RSS story.
  - Requirement references: `REQ-012`, `REQ-013`, `REQ-019`, `REQ-021`
  - Dependencies: `TASK-036`, `TASK-039`, `TASK-044`

- [ ] **TASK-053: Premium on-chain and multi-vendor derivatives adapters**
  - Objective: add the deferred premium data adapters for on-chain and broader derivatives coverage without changing launch assumptions.
  - Likely files or modules affected: `src/intelligence/sources/onchain.py`, `src/intelligence/sources/derivatives_vendor.py`, `src/intelligence/config.py`, `tests/unit/test_source_onchain.py`, `tests/unit/test_source_derivatives_vendor.py`
  - Implementation notes: support one premium on-chain provider and one premium derivatives provider behind explicit flags and secrets. Map structured vendor events into the same normalized event contract and severity rules.
  - Acceptance criteria: premium adapters can be enabled independently and produce structured candidates without changing launch-source behavior.
  - Test requirements: unit tests for provider payload parsing and integration tests for source-health behavior.
  - Requirement references: `REQ-012`, `REQ-018`, `REQ-019`, `REQ-021`
  - Dependencies: `TASK-036`, `TASK-039`, `TASK-044`

- [ ] **TASK-054: Regime classifier and regime-aware analog filtering**
  - Objective: add the deferred regime layer only as a retrieval filter, never as a discipline-rule input.
  - Likely files or modules affected: `src/intelligence/classification/regime.py`, `src/intelligence/retrieval/analog_search.py`, `src/models/signal.py`, `tests/unit/test_regime_classifier.py`, `tests/integration/test_regime_aware_retrieval.py`
  - Implementation notes: derive a periodic regime signal from price and structured data, persist it as an intelligence signal, and use it only to filter analog retrieval when enabled. Do not pass the regime signal into v1 discipline rules or alter trade lifecycle behavior.
  - Acceptance criteria: regime-aware retrieval can be enabled and disabled independently; retrieval results change when regime filtering is on, but trade-discipline decisions do not.
  - Test requirements: unit tests for regime-label generation and integration tests that compare filtered vs. unfiltered analog retrieval on the same fixture.
  - Requirement references: `REQ-010`, `REQ-015`, `REQ-016`, `REQ-020`
  - Dependencies: `TASK-042`, `TASK-044`, `TASK-052`, `TASK-053`

---

# Final Verification Checklist

- [ ] Phase-1 discipline behavior remains intact and all existing v1 tests still pass
- [ ] `REQ-010` boundary enforcement is covered by automated tests
- [ ] `/signals` returns live stored signal summaries
- [ ] `/why <signal_id>` returns attribution and analog detail from stored data only
- [ ] Launch sources include aggregator, RSS or first-party feeds, macro calendar, and Binance derivatives snapshots
- [ ] Duplicate stories collapse into one `story_key` and do not generate duplicate pushes
- [ ] Every emitted signal includes category, direction, magnitude band, confidence, horizon band, analog references, and model versions
- [ ] Signal projection is analog-backed and deterministic before any slow-lane explanation
- [ ] Slow-lane output is schema-validated, citation-bounded, and stripped of prescriptive trade advice
- [ ] Intelligence pushes remain visibly distinct from breach and monitor-health alerts
- [ ] `/health` shows intelligence enabled state, source freshness, budget state, and degraded-mode status
- [ ] Replay or backtest harness runs without look-ahead leakage and emits quality metrics
- [ ] Docker Compose runtime supports Redis Stack when intelligence is enabled
- [ ] Rollback path is documented and consists of disabling intelligence without touching trade, breach, alert, or conversation data
- [ ] Later-phase expansion tasks remain feature-flagged and out of launch definition of done unless explicitly enabled
