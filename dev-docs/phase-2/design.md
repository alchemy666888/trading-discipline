# Design: BTC Discipline Bot

## 1. Technical Summary

Phase 2 keeps the v1 architecture shape intact: a single Python service, Redis as the durable datastore, Telegram as the only user-facing interface, and the deterministic discipline rules isolated from I/O. The phase-2 addition is a feature-flagged intelligence subsystem that runs alongside the existing bot and monitor loops without crossing the REQ-010 boundary.

At runtime, the bot service now hosts three concurrent domains:

- a **Telegram bot loop** for commands and conversational forms;
- a **price monitor loop** for breach detection and monitor-health behavior;
- an **intelligence scheduler and worker set** for source polling, classification, analog retrieval, and notification decisions.

The critical architectural decision for launch is to **reuse the existing Redis deployment and enable Redis Stack vector search** instead of introducing Postgres or a dedicated vector store. This keeps operational overhead low while preserving a migration seam behind repository interfaces if later signal volume justifies a move.

The second critical design decision is that **impact projection is deterministic and analog-backed**. The LLM slow lane can refine explanations and structured fields, but it does not get to invent analogs or act as the only basis for direction, magnitude, or duration claims.

**Launch stack additions:** `httpx`, `feedparser` or equivalent RSS parser, `transformers` for the local fast lane, provider SDK(s) for embeddings and slow-lane analysis, and Redis Stack search commands through `redis.asyncio`.

## 2. Architecture

```mermaid
flowchart TD
    TG[Telegram client] <--> BOT[Bot handlers]
    BOT --> RULES[Rules engine]
    BOT --> REPO[(Redis / Redis Stack)]
    BOT <--> CONV[Conversation state]

    WS[Exchange WebSocket] --> EXC[Exchange adapter]
    EXC --> MON[Monitor loop]
    MON --> ALERT[Alert dispatcher]
    ALERT --> TG

    SRCA[Aggregator adapter]
    SRCR[RSS adapters]
    SRCM[Macro calendar]
    SRCD[Binance derivatives source]
    SRCA --> NORM[Normalize + dedupe]
    SRCR --> NORM
    SRCM --> NORM
    SRCD --> NORM
    NORM --> FAST[Fast-lane classifier]
    FAST --> RETR[Embedding + analog search]
    RETR --> PROJ[Deterministic projection]
    PROJ --> SLOW[LLM slow lane (eligible items only)]
    PROJ --> SIG[Signal writer]
    SLOW --> SIG
    SIG --> REPO
    SIG --> NOTIFY[Intelligence notifier]
    NOTIFY --> TG

    BUS{{Event bus}}
    BOT -. publish .-> BUS
    MON -. publish .-> BUS
    SIG -. publish .-> BUS
    BUS -. subscribe .-> NOTIFY
    BUS -. subscribe .-> FAST

    SCHED[APScheduler] --> SRCA
    SCHED --> SRCR
    SCHED --> SRCM
    SCHED --> SRCD
    SCHED --> REPORT[Weekly stats report]
    REPORT --> TG
```

- **Frontend:** Telegram only.
- **Backend:** one Python process with three cooperative domains under `asyncio`.
- **Database:** Redis remains authoritative for all durable state. Redis Stack vector search is enabled for intelligence launch.
- **Runtime:** Docker Compose starts the bot container and the Redis container. When intelligence is enabled, the Redis image must expose vector-search capability.
- **Background jobs:** weekly discipline report, intelligence polling jobs, macro reminder scheduling, and optional offline replay jobs.
- **External integrations:** Telegram Bot API, exchange public websocket, aggregator HTTP API, RSS feeds, macro calendar feed, optional embedding and LLM providers.
- **Auth:** Telegram whitelist remains mandatory for all user-facing commands.
- **Observability:** JSON logs to stdout, stable event names, `/health` for runtime status, source freshness, and budget-circuit visibility.

When `INTELLIGENCE_ENABLED=false`, the intelligence scheduler, source adapters, vector-index bootstrap, and notifier do not start. The rest of the system behaves as v1.

## 3. Data Model

Redis remains the authoritative datastore. The repository layer owns key construction, serialization, atomic transitions, and index lifecycle. Application modules do not issue raw Redis commands directly.

### 3.1 Key namespaces

Core v1 namespaces remain unchanged and are preserved here for completeness:

| Namespace | Type | Purpose |
|---|---|---|
| `seq:trade_id` | string integer counter | Trade IDs |
| `seq:breach_id` | string integer counter | Breach IDs |
| `seq:alert_id` | string integer counter | Alert IDs |
| `seq:signal_id` | string integer counter | Intelligence signal IDs |
| `seq:raw_event_id` | string integer counter | Normalized raw-event IDs |
| `seq:analog_id` | string integer counter | Historical analog IDs |
| `trade:{id}` | hash | Trade record |
| `trades:all` | sorted set | Trade IDs by `opened_at` |
| `trades:status:{status}` | set | Trade IDs by status |
| `trades:closed` | sorted set | Closed trade IDs by `closed_at` |
| `breach:{id}` | hash | Breach record |
| `breaches:trade:{trade_id}` | sorted set | Breaches for a trade |
| `breaches:unresolved` | set | Unresolved breaches |
| `breach:active:{trade_id}` | string | Current unresolved breach ID |
| `alert:{id}` | hash | Alert record |
| `alerts:breach:{breach_id}` | sorted set | Alerts for a breach |
| `conversation:{chat_id}` | hash | Form state |
| `schema:version` | string integer | Datastore schema version |

Phase-2 intelligence adds:

| Namespace | Type | Purpose |
|---|---|---|
| `signals:{id}` | hash | One emitted intelligence signal |
| `signals:active` | sorted set | Non-expired signal IDs by `detected_at` |
| `signals:category:{category}` | sorted set | Signal IDs for a category |
| `signals:source:{source}` | sorted set | Signal IDs by source |
| `signals:story:{story_key}` | string | Latest signal ID for a deduped story |
| `signals:raw:{id}` | hash | One normalized raw event |
| `signals:dedupe:url:{hash}` | string with TTL | Canonical URL dedupe lock |
| `signals:dedupe:content:{hash}` | string with TTL | Content dedupe lock |
| `signals:quality:{source}` | hash | Source-quality state and counters |
| `signals:health:{source}` | hash | Source freshness and last error |
| `signals:cost:{yyyy-mm-dd}` | hash | Daily model-usage and budget counters |
| `signals:budget:circuit` | hash | Slow-lane circuit-breaker status |
| `signals:analog:{id}` | hash | Curated historical analog record |
| `signals:analog:vec:{id}` | hash | Vector-search document for one analog |
| `signals:vec:index` | RediSearch index | Vector index over analog embeddings |

The `signals` namespace is the only intelligence-owned durable namespace. Intelligence code must not create or mutate keys under `trade:*`, `breach:*`, `alert:*`, or `conversation:*`.

Trade, breach, alert, and conversation record contracts remain unchanged from v1. Phase 2 extends the datastore; it does not reinterpret or loosen the v1 discipline records.

### 3.2 Record shapes

`signals:{id}` hash fields:

| Field | Type | Notes |
|---|---|---|
| `id` | integer | Generated by `seq:signal_id` |
| `story_key` | string | Stable dedupe key for the event story |
| `source` | string | Primary source name |
| `kind` | enum | `news`, `calendar`, `derivatives`, `onchain`, `regime` |
| `severity` | enum | `low`, `medium`, `high`, `critical` |
| `detected_at` | ISO-8601 timestamp | Signal creation time |
| `expires_at` | ISO-8601 timestamp | Hide from active views after expiry |
| `summary` | string | One-line Telegram-safe summary |
| `source_url` | string/null | Canonical URL when applicable |
| `analysis_status` | enum | `fast`, `slow`, `degraded` |
| `payload_json` | JSON string | Structured intelligence payload |

`payload_json` schema:

```json
{
  "schema_version": 1,
  "category": "regulatory_adverse",
  "direction": "bearish",
  "magnitude_band": "high",
  "magnitude_score": 0.62,
  "confidence": 0.71,
  "horizon_band": "days",
  "expected_half_life_seconds": 86400,
  "novelty": 0.55,
  "btc_specificity": 0.90,
  "surprise": -0.40,
  "analog_ids": [114, 142, 203],
  "analog_count": 8,
  "analog_window": "2023-01-01..2026-04-30",
  "analog_median_return_1h": -0.006,
  "analog_median_return_4h": -0.011,
  "analog_median_return_24h": -0.022,
  "analog_iqr_return_24h": [-0.041, -0.008],
  "model_versions": {
    "fast_lane": "cryptobert@launch",
    "embedding": "text-embedding-3-small",
    "slow_lane": "provider-model@launch"
  },
  "source_attribution": [
    {
      "source_name": "CoinDesk",
      "title": "SEC filing ...",
      "url": "https://...",
      "published_at": "2026-05-17T12:31:00Z"
    }
  ],
  "raw_event_key": "signals:raw:88"
}
```

`signals:raw:{id}` hash fields:

| Field | Type | Notes |
|---|---|---|
| `id` | integer | Generated by `seq:raw_event_id` |
| `source_type` | enum | `aggregator`, `rss`, `calendar`, `derivatives`, `x`, `onchain` |
| `source_name` | string | Adapter or feed name |
| `external_id` | string/null | Feed-native ID |
| `canonical_url` | string/null | URL after normalization |
| `story_key` | string | Dedupe key |
| `published_at` | ISO-8601 timestamp | Event publish time |
| `ingested_at` | ISO-8601 timestamp | Adapter ingest time |
| `title` | string | Title or synthetic summary |
| `body_text` | string | Normalized text body |
| `metadata_json` | JSON string | Adapter-specific metadata |

`signals:analog:{id}` hash fields:

| Field | Type | Notes |
|---|---|---|
| `id` | integer | Curated analog ID |
| `category` | enum | Same taxonomy as live signals |
| `event_at` | ISO-8601 timestamp | Historical event time |
| `summary` | string | Curated event summary |
| `source_url` | string/null | Reference URL |
| `regime_tag` | string/null | Used in later-phase regime filtering |
| `returns_json` | JSON string | BTC forward returns and volatility windows |
| `embedding_model` | string | Embedding model version |

### 3.3 RediSearch vector documents

Launch stores embeddings only for curated historical analog records. Live signals do not require vector insertion to be queryable by the user; only analog retrieval needs launch vector search.

`signals:analog:vec:{id}` duplicates the minimal filter fields and stores the embedding blob:

- `analog_id`
- `category`
- `event_at`
- `regime_tag`
- `embedding`

This lets RediSearch filter by category and, later, regime without unpacking JSON.

### 3.4 Atomicity rules

Repository-level atomic methods are required for:

- creating a raw normalized event and its dedupe locks;
- creating or updating a `story_key` binding;
- writing a signal record and all signal indexes together;
- updating attribution on an existing `story_key` without emitting a duplicate push;
- incrementing daily cost counters and opening or closing the slow-lane circuit breaker;
- writing source-health status and last-error metadata.

If vector-search insertion fails, the repository does **not** emit a signal that claims analog-backed projection. The event may still remain as a raw normalized item for retry.

## 4. API Design

There is still no HTTP API. The user-facing API remains the Telegram command surface.

### `/new`

Unchanged from v1. Starts the pre-trade commitment flow.

### `/closed <price>` or `/closed <id> <price>`

Unchanged from v1. Closes an open trade and resolves any active breach.

### `/justify <reason>`

Unchanged from v1. Resolves the active breach as justified and transitions the trade to `OPEN_OVERRIDE`.

### `/cancel`

Unchanged from v1. Cancels only the form state.

### `/open`

Unchanged from v1. Lists open trades.

### `/streak`

Unchanged from v1. Shows the loss streak and size cap.

### `/stats [days]`

Unchanged in meaning from v1. Shows discipline stats only.

### `/setpnl <trade_id> <pnl>`

Unchanged from v1. Manual P&L correction.

### `/health`

Extended in v2. In addition to websocket and Redis status, it includes:

- intelligence enabled/disabled;
- source freshness per enabled source;
- last successful signal time;
- slow-lane budget usage and circuit-breaker state;
- degraded-mode flag when analog retrieval or slow lane is unavailable.

### `/signals [limit]`

Launch replacement for the v1 stub.

- Default limit: 5.
- Allowed range: 1–10.
- Source of truth: persisted `signals:{id}` rows in `signals:active`.
- Sorting: severity descending, then `detected_at` descending.
- Disabled-state behavior: clear message when intelligence is off.

### `/why <signal_id>`

Returns stored detail for one signal:

- summary and category;
- direction, magnitude band, horizon band, confidence;
- primary source URL and additional attributions;
- analog IDs plus concise analog summaries;
- model version metadata;
- informational-only footer.

### `/help [cmd]`

Extended with help entries for `/signals` and `/why`.

## 5. Frontend Design

Telegram remains the frontend. The core conversational state machine for `/new` is unchanged:

```text
IDLE -> DIRECTION -> SIZE -> LEVERAGE [-> LEV_OVERRIDE] -> ENTRY -> INVALIDATION
     -> MAX_LOSS -> REGIME -> THESIS -> CONFIRM -> IDLE
```

The phase-2 frontend work is mostly message design and rendering contracts.

### 5.1 Intelligence summary rendering

`/signals` renders one compact block per signal:

```text
ℹ️ Market intelligence
#184 high regulatory_adverse | bearish | days | conf 0.71
SEC filing signals delayed approval timetable; similar events saw median -2.2% at 24h.
Source: CoinDesk + SEC EDGAR
```

Rendering rules:

- include `analysis_status=degraded` inline when applicable;
- never exceed the configured command message size limit; truncate older signals before truncating a single signal's essential fields;
- always end with an informational-only footer and `/why` hint.

### 5.2 Intelligence detail rendering

`/why` renders a longer detail block:

- first line: `ℹ️ Market intelligence #{id}` or `⚠️ Market intelligence #{id}`;
- second line: category, bias, magnitude, horizon, confidence;
- third line: concise explanation;
- then analog IDs and analog return summary;
- then attribution URL(s);
- final line: informational-only footer.

### 5.3 Scheduled macro reminder rendering

Scheduled macro reminders are special-case signals generated from the calendar source:

```text
⚠️ Market intelligence
FOMC release in 15m. You have 1 open trade.
Category: macro_scheduled | horizon: hours
Informational only — discipline rules unchanged.
```

They are not breach alerts and must never use the breach-alert headline or cadence.

### 5.4 Empty, disabled, and degraded states

- `/signals` with intelligence disabled -> `"Intelligence is disabled. Enable INTELLIGENCE_ENABLED to start signal capture."`
- `/signals` with no active signals -> `"No active intelligence signals. Use /health to confirm sources are healthy."`
- `/why` for missing signal -> `"Signal {id} not found or expired."`
- Degraded signal -> include `(degraded analysis)` in the header or summary line.

## 6. Backend Design

### 6.1 Module layout

```text
src/
  app.py
  config.py
  bot/
    handlers.py
    forms.py
    formatting.py
    whitelist.py
  monitor/
    monitor.py
    breach.py
    alerts.py
    health.py
  exchange/
    base.py
    binance.py
    bybit.py
  intelligence/
    __init__.py
    config.py
    orchestrator.py
    scheduler.py
    sources/
      base.py
      aggregator.py
      rss.py
      macro_calendar.py
      binance_derivatives.py
      x_stream.py          # later phase
      onchain.py           # later phase
    ingestion/
      normalizer.py
      deduper.py
      quality.py
    classification/
      fastlane.py
    analysis/
      llm.py
      policy.py
    retrieval/
      embeddings.py
      vector_repo.py
      analog_search.py
    impact/
      projection.py
    emitters/
      notifier.py
  models/
    trade.py
    breach.py
    alert.py
    conversation.py
    signal.py
    news_event.py
    analog.py
    events.py
  db/
    keyspace.py
    migrations.py
    repo.py
    scripts/
tests/
  unit/
  integration/
  e2e/
```

### 6.2 Service boundaries

- `rules/` stays pure and deterministic.
- `db/repo.py` remains the only module that touches Redis directly.
- `intelligence/` may call repository methods that target signal-owned namespaces only.
- `bot/handlers.py` renders signals but never calls embedding or LLM clients inline.
- `monitor/` and `intelligence/` are peers; neither is allowed to alter the other's authoritative state.

### 6.3 Runtime wiring

`src/app.py` is responsible for:

- loading base and intelligence configuration;
- verifying Redis and, when enabled, Redis Stack vector capability;
- bootstrapping schema and search indexes;
- constructing the event bus;
- starting monitor, bot, and intelligence domains;
- ensuring intelligence workers are omitted entirely when disabled.

## 7. Intelligence Pipeline Design

### 7.1 Source adapters

Launch source adapters:

- `aggregator.py` — polls one configured aggregator provider every 60–120 seconds.
- `rss.py` — polls configured RSS feeds every 60 seconds with ETag or `Last-Modified` support when possible.
- `macro_calendar.py` — refreshes the configured calendar periodically and emits reminder candidates when the configured event window is entered.
- `binance_derivatives.py` — samples funding and OI snapshots on a configured cadence and emits structured candidates when thresholds are crossed.

Later-phase adapters:

- `x_stream.py` — curated X/Twitter-alternative feed.
- `onchain.py` — Whale Alert, CryptoQuant, CoinGlass, or similar.

Each adapter emits `RawSourceItem` objects with stable source IDs, source timestamps, and enough raw text or metadata for normalization.

### 7.2 Normalization and dedupe

`normalizer.py` converts heterogeneous source items into one `NewsEvent` shape:

- strip tracking parameters from URLs;
- collapse repeated whitespace and HTML noise;
- extract or synthesize a title;
- attach source-type metadata;
- derive a provisional `story_key`.

`deduper.py` then:

1. checks canonical URL lock;
2. checks normalized content-hash lock;
3. merges with an existing `story_key` when either lock matches;
4. assigns a `novelty` score based on similarity to recent signals;
5. updates source-attribution lists on the existing story when needed.

Default launch TTL for dedupe locks: 24 hours. This is long enough to suppress story storms without hiding genuinely new follow-up stories on later days.

### 7.3 Source quality handling

`quality.py` maintains:

- configured baseline trust by source;
- rolling counts of accepted, deduped, suppressed, and errored items;
- optional manual blocklist or down-rank overrides.

Launch design keeps source quality simple and deterministic:

- hard block known-noise sources;
- configurable minimum quality score to advance past normalization;
- update counters for observability;
- no self-training feedback loop in launch scope.

### 7.4 Fast-lane classification

`classification/fastlane.py` handles every eligible candidate.

Text-bearing sources use a local transformer classifier to produce:

- category;
- direction;
- confidence;
- BTC specificity;
- optional provisional magnitude score.

Structured launch sources use deterministic mappings:

- macro reminders -> `macro_scheduled`;
- funding or OI thresholds -> `funding_oi_extreme`.

Launch thresholds, all configurable, gate progression to the next step:

- `btc_specificity >= 0.60`
- `novelty >= 0.35`
- `source_quality >= 0.40`

Candidates below threshold are stored as raw events for replay and observability but do not become signals.

### 7.5 Analog retrieval and deterministic projection

The analog system is the primary basis for projected impact.

#### Retrieval

`embeddings.py` embeds the normalized event text. `analog_search.py` queries RediSearch over `signals:analog:vec:{id}`:

- filter 1: category match is mandatory;
- filter 2: optional regime tag in later phase;
- ranking: cosine similarity descending;
- launch default: `top_k = 8`.

#### Projection

`impact/projection.py` derives signal projections from the retrieved analog set.

Direction:

- sign of the median 24-hour analog return;
- near-zero median return -> `neutral`.

Magnitude band:

- `low` -> median absolute 24-hour return < 1%
- `medium` -> 1% to < 3%
- `high` -> 3% to < 6%
- `extreme` -> >= 6%

Duration:

- compute the peak median absolute move across the forward windows;
- estimate the first window where the median absolute move decays below 50% of that peak;
- map `<= 24h` -> `hours`, `<= 7d` -> `days`, otherwise `weeks`.

This logic is deterministic and testable. The slow lane may explain it, but it does not replace it.

### 7.6 Slow-lane analysis

The slow lane is optional and bounded.

Eligibility rules:

- category is high-priority (`regulatory_*`, `exchange_security`, `macro_*`, `funding_oi_extreme`);
- or the fast lane returns moderate confidence but high BTC specificity and novelty;
- or there are open trades and the provisional magnitude is at least `medium`.

`analysis/llm.py` receives:

- normalized event text in clearly delimited untrusted blocks;
- retrieved analog summaries and IDs;
- deterministic projection outputs;
- a system instruction that forbids prescriptive trade advice and forbids citations outside the supplied analog IDs.

Returned JSON may refine:

- summary wording;
- confidence;
- surprise;
- explanatory text;
- final category choice inside the allowed taxonomy.

The slow lane may not invent analog IDs, invent URLs, or override the no-trading-advice policy.

### 7.7 Severity and notification policy

Severity is assigned deterministically before notification:

- `critical`
  - exchange-security or macro-unscheduled event with `magnitude_band in {high, extreme}` and confidence >= 0.60; or
  - macro reminder at T-15m while at least one trade is open.
- `high`
  - magnitude >= `medium` with open trades; or
  - high-confidence funding/OI extreme with open trades.
- `medium`
  - magnitude >= `medium` without open trades; or
  - materially BTC-specific launch-source item that passes the fast lane but does not meet push criteria.
- `low`
  - everything else that is still worth storing.

Notification rules:

- one push per `story_key`;
- category cooldown default 30 minutes;
- `critical` pushes regardless of open-trade state;
- `high` pushes only when open trades exist;
- `medium` pushes only for scheduled macro reminders with open trades;
- `low` stores only.

`emitters/notifier.py` reads current open-trade count from the repository rather than from mutable intelligence-owned state.

### 7.8 Event-bus integration

Launch intelligence subscribes to these existing events:

| Event | Purpose |
|---|---|
| `trade_opened` | Recompute exposure-aware notification decisions |
| `trade_closed` | Recompute exposure-aware notification decisions |
| `monitor_down` | Suppress low-value intelligence pushes while a critical monitor outage is active |
| `monitor_recovered` | Resume normal intelligence notification policy |

Launch intelligence publishes:

| Event | Purpose |
|---|---|
| `news_received` | One raw normalized item was accepted from a source |
| `signal_candidate` | Candidate passed normalization and fast-lane gates |
| `signal_emitted` | Final signal persisted |
| `signal_suppressed` | Signal stored or dropped without push because of policy |
| `source_unhealthy` | Source adapter entered unhealthy state |
| `budget_circuit_opened` | Slow lane disabled because budget was exceeded |
| `budget_circuit_closed` | Slow lane re-enabled after reset |

No discipline rule subscribes to these intelligence-published events.

## 8. Security Design

- **Auth:** same Telegram whitelist as v1.
- **Source input trust model:** all source text is untrusted. HTML is stripped, text length is capped, and text is never treated as executable instructions.
- **Prompt-injection defense:** slow-lane prompts wrap source text in clearly labeled untrusted blocks. System instructions explicitly tell the model to ignore any instructions found inside source content.
- **Structured-output enforcement:** slow-lane output must pass JSON schema validation. Invalid output is retried once, then downgraded or dropped.
- **Citation fidelity:** slow-lane output may cite only analog IDs supplied by retrieval and source URLs already present in repository records.
- **No trade-action policy:** `analysis/policy.py` rejects or downgrades outputs containing prescriptive trade language.
- **Secrets:** provider API keys live in env vars or `.env` and are never logged.
- **Redis safety:** all keys are constructed by repository helpers. User or feed text is stored only as values.

## 9. Error Handling

| Error | Handling |
|---|---|
| Aggregator 429 or timeout | Mark source unhealthy, log, retry on next poll, continue other sources. |
| RSS parse error | Mark only that feed unhealthy, log parse error, continue. |
| Macro calendar unavailable | Suppress new macro reminders, mark degraded in `/health`, continue non-calendar sources. |
| Binance derivatives snapshot failure | Log and retry; do not affect trade monitoring websocket. |
| Dedupe collision | Merge attribution into existing `story_key`; do not create a second push. |
| Redis Stack capability missing while intelligence enabled | Fail startup with a clear message. |
| Embedding timeout or provider error | Do not claim analog-backed projection; store raw item for retry; mark degraded if necessary. |
| Slow-lane timeout or parse failure | Retry once, then emit `analysis_status=degraded` or suppress according to severity policy. |
| Slow-lane budget exceeded | Open circuit breaker, skip new slow-lane calls until reset, keep fast lane active. |
| Telegram send failure for intelligence push | Retry 3x with backoff; persistent failure logs ERROR but signal remains stored. |
| `/why` on missing or expired signal | Return a clear user-facing message; no exception leak. |
| Intelligence subsystem crash | Top-level exception handling logs the crash and keeps bot and monitor domains alive; scheduler attempts a bounded restart. |

In all cases, intelligence failures are treated as informational degradation only. They must not block trade creation, breach detection, breach escalation, or discipline reporting.

## 10. Testing Strategy

### 10.1 Unit tests

- normalization and URL canonicalization;
- dedupe lock behavior and `story_key` merging;
- source-quality filters;
- fast-lane classifier mapping;
- deterministic severity assignment;
- analog projection thresholds and duration mapping;
- slow-lane JSON validation and policy rejection of prescriptive language;
- signal rendering for `/signals` and `/why`;
- boundary enforcement that intelligence cannot write protected namespaces.

### 10.2 Integration tests

- repository signal lifecycle and Redis Stack index bootstrap;
- aggregator, RSS, macro-calendar, and derivatives adapters with fixtures;
- orchestrator path from raw event to stored signal;
- notifier behavior against mocked Telegram client;
- `/health` reporting for enabled, disabled, and degraded intelligence states;
- budget-circuit transitions and persistence.

### 10.3 End-to-end tests

Required scenarios:

- aggregator article -> fast lane -> analog retrieval -> stored signal -> `/signals`;
- duplicate article through aggregator and RSS -> one `story_key`, one notification;
- macro reminder with open trade -> immediate push;
- macro reminder with no open trade -> stored only;
- slow-lane failure -> degraded signal still queryable;
- Redis Stack unavailable while intelligence enabled -> startup failure;
- budget exhaustion -> slow lane disabled, fast lane still active;
- monitor-down event active -> low-value intelligence pushes suppressed;
- `/why` renders expected analog citations for a known replay fixture.

### 10.4 Replay and backtest tests

The replay harness must:

- replay held-out events in timestamp order with frozen model versions;
- forbid look-ahead in analog retrieval;
- compute direction accuracy, calibration, and horizon-band accuracy;
- emit a report suitable for deciding whether push notifications remain enabled.

## 11. Migration and Rollout Plan

Phase-2 rollout is staged even though the codebase supports the full architecture.

### 11.1 Deployment sequence

1. Upgrade Compose to a Redis Stack-capable image and bootstrap indexes with `INTELLIGENCE_ENABLED=false`.
2. Deploy phase-2 code and verify that v1 flows are unchanged.
3. Enable intelligence with launch sources and `/signals`, but keep slow lane disabled if needed.
4. Load curated historical analog records and verify retrieval quality.
5. Enable slow lane behind budget caps.
6. Add later-phase sources one at a time behind separate flags.

### 11.2 Rollback

Rollback path is intentionally simple:

- set `INTELLIGENCE_ENABLED=false`;
- restart the bot;
- keep Redis data intact.

This removes intelligence behavior without affecting trades, breaches, alerts, or conversation state.

### 11.3 Migration boundary

If signal volume later outgrows Redis Stack, only these seams should need to change:

- `intelligence/retrieval/vector_repo.py`
- intelligence-specific repository methods in `db/repo.py`
- intelligence-specific configuration and health checks

Bot handlers, rules, monitor, and discipline storage must not require changes for that migration.
