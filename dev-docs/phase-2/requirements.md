# Requirements: BTC Discipline Bot

## 1. Overview

The **BTC Discipline Bot** remains a personal behavioral commitment tool for a discretionary BTC perpetual futures trader. Its core job is still to enforce the user's own trading rules at the two moments where discretionary traders self-destruct:

1. **Entry** — by requiring a full pre-trade checklist before a trade can be marked open.
2. **Exit** — by monitoring BTC price and aggressively pinging the user when their stated invalidation is breached.

Phase 2 adds a second, clearly-separated capability: a **read-only intelligence layer** that captures relevant BTC market events, classifies them, estimates likely impact using historical analogs, and informs the user through Telegram. It does **not** decide whether a trade is allowed, blocked, resized, justified, or closed.

The system therefore has two responsibilities with a hard boundary between them:

- **Discipline enforcement:** deterministic, rule-based, binding.
- **Market intelligence:** probabilistic, informational, non-binding.

The value of the product depends on that separation remaining explicit in product behavior, storage boundaries, messaging style, and code structure.

- **Who it is for:** a single discretionary BTC trader whose losses come primarily from execution and discipline failures.
- **Problem solved in v1:** enforce pre-commitment and post-invalidation accountability.
- **Problem added in v2:** surface relevant BTC news and adjacent risk signals early enough, and with enough context, that the user is better informed without weakening the discipline system.

## 2. Goals

- **G1** Make it impossible to mark a trade "opened" without a complete pre-trade checklist.
- **G2** Default-block leverage >= 20X; require explicit override with a typed reason.
- **G3** After 2 consecutive losing closed trades, force the next trade's size to be <= 50% of the most recent winning trade's size.
- **G4** Detect invalidation breach within 5 seconds of the breach price printing on the exchange feed.
- **G5** Deliver an aggressive breach alert within 10 seconds of an invalidation breach.
- **G6** Require the user to either mark the trade closed or write a free-text justification when invalidation has been breached.
- **G7** Persist every commitment, every breach, and every user response so adherence statistics can be computed.
- **G8** Remain single-user, low-cost, self-hostable, and Telegram-first.
- **G9** Capture relevant BTC market news and adjacent structured signals from a conservative launch source set.
- **G10** Classify event type and sentiment in a way that is explicit, testable, and auditable.
- **G11** Estimate likely direction, magnitude band, and duration band of impact using historical analogs rather than free-form model intuition alone.
- **G12** Deliver intelligence through Telegram in a style that is visibly distinct from deterministic breach alerts.
- **G13** Keep the intelligence layer feature-flagged and operationally isolated so v1 behavior remains intact when intelligence is disabled or degraded.
- **G14** Bound intelligence cost and noise through source filtering, deduplication, throttling, and daily model-budget controls.

## 3. Non-Goals

- **NG1** Not a trading bot. Does not connect to an exchange to place, modify, or cancel orders.
- **NG2** Not a trade advisor. It may describe likely BTC impact from events, but it must not tell the user to open, close, size, hedge, or justify a trade.
- **NG3** BTCUSDT perpetual only.
- **NG4** Single user, single Telegram chat. No multi-tenant support, no roles.
- **NG5** Does not auto-close positions on breach; the user closes manually.
- **NG6** No native mobile app and no web UI in phase 2. Telegram remains the only user-facing interface.
- **NG7** No multi-asset watchlist, portfolio analytics, or cross-exchange execution dashboard.
- **NG8** No LLM-driven modification of leverage rules, size rules, invalidation rules, breach escalation rules, or adherence statistics.
- **NG9** No full social-media firehose at launch. Curated X/Twitter-alternative ingestion is a later-phase expansion.
- **NG10** No mandatory paid on-chain or ETF-flow vendor dependency at launch. Third-party premium data is a later-phase expansion.
- **NG11** No autonomous agent behavior that browses, chats, or changes system state outside the bounded source adapters and configured analysis pipeline.

## 4. Users and Use Cases

**Primary user:** a single discretionary BTC trader.

- As a discretionary trader, I want to be forced to fill in my invalidation before I can mark a trade open, so I cannot enter without a pre-committed exit plan.
- As a discretionary trader, I want the bot to block 20X leverage by default, so my impulsive leverage escalation meets friction.
- As a discretionary trader, I want the bot to enforce size reduction after consecutive losses, so revenge-sizing is structurally prevented.
- As a discretionary trader, I want to be pinged aggressively when my invalidation is breached, so I cannot quietly hold a loser past my own stated limit.
- As a discretionary trader, I want to be forced to write a justification if I refuse to close at invalidation, so breaking my own rule has a friction cost and a record.
- As a discretionary trader, I want a weekly adherence report, so I can measure whether the tool is changing my behavior.
- As a discretionary trader, I want the bot to notify me when materially relevant BTC market events happen, so I have better context while positions are open.
- As a discretionary trader, I want intelligence messages to tell me what happened, why it matters, and how long similar events historically mattered, so I can decide for myself how much attention to pay.
- As a discretionary trader, I want to inspect the cited analogs behind a signal, so I can trust but verify the system.
- As a discretionary trader, I do not want informational intelligence to be confused with breach enforcement, because discipline alerts must stay unambiguous.

## 5. Scope and Rollout Boundaries

### 5.1 Launch scope

Phase 2 launch includes:

- one aggregator API adapter behind a provider abstraction, defaulting to a CryptoPanic-compatible source;
- selected direct RSS and first-party feeds for crypto-native outlets, regulators, and exchange announcements;
- a macro calendar source for scheduled US events relevant to BTC (`FOMC`, `CPI`, `NFP`, `PCE`);
- Binance-derived funding-rate and open-interest snapshots as the only launch structured derivatives feed;
- strong URL plus content-level deduplication, normalization, and source attribution;
- a two-lane analysis architecture: local fast-lane classification first, LLM escalation second;
- a curated historical analog dataset for launch rather than a massive uncontrolled backfill;
- real `/signals` behavior, a new `/why <signal_id>` command, and carefully throttled Telegram intelligence pushes;
- Redis-aligned signal storage with Redis Stack vector search enabled for launch when `INTELLIGENCE_ENABLED=true`.

### 5.2 Later-phase expansion inside phase 2

Later phase-2 expansions may add:

- curated X/Twitter-alternative ingestion;
- premium on-chain and derivatives adapters such as Whale Alert, CryptoQuant, or CoinGlass;
- regime-aware analog filtering using a dedicated regime classifier;
- richer weekly intelligence review messaging once launch precision and noise levels are acceptable.

These expansions remain feature-flagged and must not be required for launch acceptance.

### 5.3 Out of scope

Still out of scope in phase 2:

- trade execution or exchange account automation;
- a web UI;
- multi-asset support;
- AI-generated enforcement decisions;
- open-ended agent workflows outside the fixed intelligence pipeline.

## 6. Functional Requirements

### REQ-001 — Pre-trade commitment form

**Statement:** Before a trade can transition to status `OPEN`, the user must complete a structured commitment form via a Telegram conversation. All fields are validated before the trade is created.

**Required fields:**

| Field | Type | Constraints |
|---|---|---|
| direction | enum | `long` or `short` |
| size_usdt | number | `> 0`, notional |
| leverage | integer | `1–125` and subject to REQ-002 |
| entry_price | number | `> 0` |
| invalidation_price | number | `> 0`, must be `< entry` for long, `> entry` for short |
| max_loss_usdt | number | `> 0` |
| regime | enum | `uptrend`, `range`, `downtrend`, `event_risk` |
| thesis | string | `10–280` chars |

**Expected behavior:** The bot walks the user through fields sequentially. Each field is validated before the next prompt. Once all fields pass validation and discipline rules, the trade transitions to `OPEN` and the price monitor registers the invalidation level.

**Edge cases:**

- User abandons form mid-flow -> expire after 10 minutes idle; no trade created.
- Invalidation on wrong side of entry -> reject with explanation and re-prompt.
- Multiple simultaneously-open trades -> allowed; each monitored independently. Warn user when opening a second trade.

**Acceptance criteria:**

- A trade with status `OPEN` cannot exist in the datastore without all 8 fields populated and validated.
- Validation errors produce clear messages naming the failing field and the rule.
- Monitor subscription is registered within 1 second of `OPEN` transition.

### REQ-002 — Leverage block

**Statement:** Leverage values >= 20X are blocked by default. To proceed, the user must type a one-line reason (>= 10 chars) explaining why high leverage is justified for that specific trade. The reason is persisted.

**Expected behavior:** When leverage >= threshold is entered, the bot sends a fixed warning and waits for either a valid reason or `/cancel`.

**Edge cases:**

- Leverage > 125 -> reject.
- Non-integer or non-positive leverage -> reject.
- `/cancel` -> return to leverage prompt; no trade created.
- Threshold value itself is blocked through a strict `>=` comparison.

**Acceptance criteria:**

- No trade with leverage >= threshold can be created without a non-empty `leverage_override_reason`.
- Override reasons are visible in discipline stats output.

### REQ-003 — Consecutive-loss size reduction

**Statement:** After 2 consecutive closed trades with realized P&L < 0, the next trade must use `size_usdt` <= 50% of the size of the most recent winning trade. If no winning trade exists, the cap is 50% of the average of the last 5 trades or 50% of the max size ever used, whichever is smaller.

**Expected behavior:** During size entry, the rule engine queries trade history. If the streak threshold has been met, the bot computes the cap and rejects any entered size above it.

**Edge cases:**

- Break-even trade does not reset and does not increment the streak.
- Fewer than 5 prior trades and no winners -> cap = 50% of max size used so far.
- No prior trades at all -> no cap.
- Manual P&L override via `/setpnl` recomputes the streak.

**Acceptance criteria:**

- Trade creation is rejected if size exceeds the active cap.
- Enforcement is logged on the trade record.
- `/streak` shows current streak and active cap.

### REQ-004 — Active price monitor

**Statement:** While any trade is in status `OPEN` or `OPEN_OVERRIDE`, the system maintains a websocket subscription to BTCUSDT perpetual price ticks on the configured exchange. On each tick, every open trade's invalidation is evaluated.

**Expected behavior:**

- Subscribe on service start.
- Long breach iff tick price <= invalidation.
- Short breach iff tick price >= invalidation.
- First breach for a trade fires the breach handler.
- Additional ticks on the same side of invalidation do not create duplicate breach records while the breach is unresolved.
- After the user responds with `/closed` or `/justify`, monitoring re-arms for a later re-breach.

**Edge cases:**

- Websocket disconnect -> exponential backoff reconnect.
- Stale tick (> 30 seconds old) -> discard and force reconnect.
- Price gap through invalidation between ticks -> first tick on the wrong side counts as breach.
- Coverage gap > 60 seconds -> on the first tick after reconnect, re-evaluate every open trade.

**Acceptance criteria:**

- Breach is detected within 5 seconds of the breach tick arriving from the exchange.
- Disconnections are logged and gaps > 60 seconds appear in `/health`.

### REQ-005 — Breach alert and forced response

**Statement:** When a breach is detected, the bot sends an aggressive Telegram alert and requires `/closed <price>` or `/justify <reason>`. If neither arrives, alerts escalate.

**Expected behavior:**

- First alert is immediate.
- Re-send every 60 seconds for the first 5 minutes, then every 5 minutes thereafter.
- `/closed` transitions the trade to `CLOSED`, records close data, computes realized P&L, and stops escalation.
- `/justify` transitions the trade to `OPEN_OVERRIDE`, persists justification text, stops the current escalation sequence, and resumes monitoring.

**Edge cases:**

- Price recovers before user responds -> alerts continue; the breach already happened.
- User starts `/new` mid-breach -> allowed; `/closed` and `/justify` still take precedence.
- User closes on exchange but never tells the bot -> alerts continue by design.

**Acceptance criteria:**

- Exactly one breach record exists per breach event.
- Escalation cadence is testable with a frozen clock.

### REQ-006 — Telegram command surface

**Statement:** The bot supports these Telegram commands:

| Command | Purpose |
|---|---|
| `/new` | Start commitment form |
| `/closed <price>` or `/closed <id> <price>` | Close most recent or specific open trade |
| `/justify <reason>` | Submit breach justification |
| `/cancel` | Cancel in-progress form |
| `/open` | List currently open trades |
| `/streak` | Show consecutive-loss counter and active size cap |
| `/stats [days]` | Show discipline adherence and P&L stats |
| `/setpnl <trade_id> <pnl>` | Manual P&L override |
| `/health` | Show monitor, Redis, and intelligence health |
| `/signals [limit]` | Show active or recent intelligence signals |
| `/why <signal_id>` | Show the cited analogs and source attribution behind one signal |
| `/help [cmd]` | Show command list or per-command help |

**Expected behavior:**

- `/stats` remains a discipline report. Intelligence signals do not change adherence metrics.
- `/signals` returns the most recent non-expired signals, ordered by severity then recency, with summary, category, direction, magnitude band, horizon band, confidence, and attribution.
- `/why <signal_id>` returns the stored explanation for a signal, including cited source URL(s), analog IDs, analog summaries, and the informational-only footer.
- If intelligence is disabled, `/signals` and `/why` reply with a clear disabled-state message rather than failing.

**Acceptance criteria:**

- All commands respond in under 2 seconds under normal load.
- `/signals` and `/why` read already-persisted signal data and do not perform live model calls inline.
- Every command has a help string accessible through `/help <command>`.

### REQ-007 — Adherence stats

**Statement:** The `/stats` command and the weekly discipline summary deliver the following metrics over a rolling window:

- total trades, wins, losses, breakeven;
- win rate, total realized P&L;
- breach count and adherence rate;
- leverage override count;
- size-reduction enforcement count and compliance rate;
- P&L broken down by stated regime.

**Boundary rule:** intelligence signals, signal sentiment, and signal outcomes must not change these deterministic discipline metrics or be merged into the adherence denominator.

**Acceptance criteria:**

- Stats are deterministic on the same input.
- Weekly discipline summary is pushed every Monday 09:00 in the configured timezone.

### REQ-008 — Core runtime configuration

**Statement:** Core discipline and runtime parameters are configurable at startup via env vars or `.env`:

| Var | Default |
|---|---|
| `TELEGRAM_BOT_TOKEN` | required |
| `TELEGRAM_CHAT_ID` | required |
| `EXCHANGE` | `binance` |
| `SYMBOL` | `BTCUSDT` |
| `LEVERAGE_BLOCK_THRESHOLD` | `20` |
| `CONSECUTIVE_LOSS_THRESHOLD` | `2` |
| `SIZE_REDUCTION_FACTOR` | `0.5` |
| `FORM_TIMEOUT_SECONDS` | `600` |
| `ALERT_INTERVAL_FIRST_WINDOW_SECONDS` | `60` |
| `ALERT_INTERVAL_FIRST_WINDOW_DURATION_SECONDS` | `300` |
| `ALERT_INTERVAL_AFTER_SECONDS` | `300` |
| `MONITOR_DOWN_ALERT_DELAY_WITH_OPEN_TRADES_SECONDS` | `10` |
| `MONITOR_DOWN_ALERT_DELAY_NO_OPEN_TRADES_SECONDS` | `60` |
| `MONITOR_DOWN_REPEAT_WITH_OPEN_TRADES_SECONDS` | `60` |
| `MONITOR_DOWN_REPEAT_NO_OPEN_TRADES_SECONDS` | `300` |
| `HEARTBEAT_TIME_LOCAL` | `09:00` |
| `TIMEZONE` | `UTC` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `REDIS_DATA_DIR` | `./data/redis` |
| `REDIS_APPENDONLY` | `yes` |
| `COMPOSE_PROJECT_NAME` | `btc-discipline-bot` |
| `INTELLIGENCE_ENABLED` | `false` |

**Acceptance criteria:**

- Invalid values fail startup with a clear error.
- Changing configuration requires restart only.
- When `INTELLIGENCE_ENABLED=false`, REQ-001 through REQ-009 behave exactly as they did in v1.

### REQ-009 — Monitor health notifications

**Statement:** When the price monitor cannot evaluate breaches, the user is notified via Telegram. Notification timing is tiered by exposure so transient network blips do not generate noise, but real outages reach the user quickly when positions are at risk.

**Tier table:**

| State | Open trades? | First alert delay | Repeat interval |
|---|---|---|---|
| WS disconnect or stale ticks | Yes | 10 seconds | every 60 seconds while down |
| WS disconnect or stale ticks | No | 60 seconds | every 5 minutes while down |
| Reconnect after previous alert | Either | recovery message immediately | n/a |
| Daily heartbeat (healthy) | Either | fires at `HEARTBEAT_TIME_LOCAL` | once per day |
| Daily heartbeat (unhealthy) | Either | suppressed | n/a |

**Acceptance criteria:**

- A simulated 15-second disconnect with one open trade produces one initial alert and one recovery message.
- A simulated 8-second disconnect with one open trade produces no Telegram alert.
- A simulated 70-second disconnect with no open trades produces one alert and one recovery message.
- Reconnect after a >60-second gap re-evaluates open trades on the first new tick.

### REQ-010 — Read-only intelligence boundary

**Statement:** The intelligence layer is an informational subsystem. It may ingest external data, classify events, retrieve historical analogs, write to the `signals` namespace, and publish intelligence-related events. It must remain read-only with respect to discipline enforcement.

**Hard architectural rules:**

- The intelligence layer may write to `signals` and intelligence-only supporting keys.
- It must not write to `trades`, `breaches`, `alerts`, or `conversation_state`.
- It must not decide whether a trade is opened, blocked, sized, modified, justified, or closed.
- Discipline rules in `src/rules/` remain deterministic and must produce identical decisions regardless of intelligence output.
- Intelligence Telegram messages must remain visually and verbally distinct from breach alerts.

**Acceptance criteria:**

- With intelligence enabled, all discipline behaviors in REQ-001 through REQ-009 still operate as specified.
- A convention test verifies that intelligence modules cannot write protected trade, breach, alert, or conversation namespaces.
- Rule tests demonstrate that populated signal context does not change v1 discipline decisions.
- Intelligence messages always include an informational-only footer or equivalent wording that makes the non-binding nature explicit.

### REQ-011 — Docker Compose runtime, Redis persistence, and Redis Stack launch choice

**Statement:** The project must run through Docker Compose and use Redis as the durable application datastore. When intelligence is enabled, the launch architecture uses the same Redis deployment with Redis Stack vector-search capability enabled rather than adding a second operational datastore.

**Expected behavior:**

- Docker Compose starts both the bot service and a Redis service.
- Redis remains the authoritative store for trades, breaches, alerts, conversation state, and signals.
- Redis persistence is enabled with append-only file persistence and a host-mounted data directory.
- When `INTELLIGENCE_ENABLED=true`, startup verifies that the configured Redis instance exposes the vector-search capability required for analog retrieval.
- Signal-storage growth is isolated behind repository interfaces so a future migration to another vector store can occur without changing discipline modules.

**Acceptance criteria:**

- `docker compose up -d` starts the stack without a local Python or Redis install.
- Creating a trade, stopping the stack, and starting it again preserves the trade.
- When intelligence is enabled and Redis Stack capability is missing, startup fails clearly before accepting Telegram commands.
- `/health` reports Redis connectivity, persistence status, and intelligence-vector capability status.

### REQ-012 — Intelligence ingestion scope and source strategy

**Statement:** Phase-2 launch ingests market intelligence from a conservative source set chosen for signal quality, operational simplicity, and cost control.

**Launch source categories:**

- **Aggregator API:** one provider abstraction, defaulting to a CryptoPanic-compatible adapter.
- **Direct RSS / first-party feeds:** selected crypto-native outlets, regulator press or filing feeds, and exchange announcement feeds.
- **Macro calendar:** scheduled US macro events relevant to BTC (`FOMC`, `CPI`, `NFP`, `PCE`).
- **Derivatives feed:** Binance-derived funding-rate and open-interest snapshots.

**Deferred to later phase 2:**

- curated X/Twitter-alternative live feed ingestion;
- premium on-chain feeds and premium ETF-flow vendors;
- broader multi-vendor derivatives coverage beyond the Binance launch source.

**Expected behavior:**

- Every launch source emits normalized events with source attribution, timestamps, and a stable source type.
- Macro calendar events produce pre-event reminders for the configured event set when the event enters the reminder window.
- Funding-rate and OI snapshots are evaluated on a rolling basis and may emit structured signals when configured thresholds are crossed.

**Acceptance criteria:**

- Launch adapters can ingest, normalize, and persist events for all four launch source categories.
- Macro reminder signals can be generated without requiring a live news headline.
- Enabling or disabling any intelligence source does not affect v1 trade-discipline behavior.

### REQ-013 — Deduplication, normalization, and source quality handling

**Statement:** The intelligence layer must prevent duplicate amplification and noisy low-value signals before classification or notification.

**Expected behavior:**

- Deduplicate by canonical URL when available.
- Deduplicate by normalized content hash when URL differs but article text is effectively the same story.
- Maintain a `story_key` so multiple source records can map to one market event.
- Compute and persist a `novelty` score for each candidate signal.
- Maintain a source-quality score and allow low-quality sources to be filtered or down-ranked.
- If later sources add useful attribution to an existing `story_key`, merge attribution without generating a second push notification for the same story.

**Acceptance criteria:**

- The same Reuters-style story seen through multiple launch feeds results in one active signal.
- Later restatements can enrich attribution without triggering duplicate pushes inside the configured cooldown window.
- Source-quality filtering is deterministic and configurable.

### REQ-014 — Classification and sentiment architecture

**Statement:** The intelligence layer uses a two-lane analysis architecture: a local fast lane classifies every eligible event, and an LLM slow lane is used only for escalated items.

**Classification taxonomy:**

- `regulatory_favorable`
- `regulatory_adverse`
- `exchange_security`
- `macro_scheduled`
- `macro_unscheduled`
- `etf_flow`
- `funding_oi_extreme`
- `corporate_treasury`
- `protocol_bitcoin`
- `whale_onchain`
- `exchange_announcement`
- `market_structure_other`

**Signal output shape:**

| Field | Type |
|---|---|
| `category` | taxonomy enum |
| `direction` | `bullish`, `bearish`, `neutral` |
| `magnitude_band` | `low`, `medium`, `high`, `extreme` |
| `magnitude_score` | `0.0 .. 1.0` |
| `confidence` | `0.0 .. 1.0` |
| `horizon_band` | `hours`, `days`, `weeks` |
| `expected_half_life_seconds` | integer |
| `novelty` | `0.0 .. 1.0` |
| `btc_specificity` | `0.0 .. 1.0` |
| `surprise` | `-1.0 .. +1.0` or `null` when unavailable |
| `summary` | <= 280 chars |

**Expected behavior:**

- The fast lane runs on every eligible normalized event and assigns preliminary category, direction, confidence, novelty, and BTC specificity.
- Structured launch sources such as macro reminders and funding/OI thresholds may use deterministic classifiers instead of free-text models.
- The slow lane is called only for escalated items, such as high-priority categories, ambiguous fast-lane outputs, or high-relevance stories while the user has open trades.
- If the slow lane fails, the system may still emit a fast-lane-only signal marked as degraded; it must not block discipline operations.

**Acceptance criteria:**

- Every emitted signal includes the full required output shape.
- LLM escalation is bounded by configured eligibility and budget rules.
- Slow-lane failure does not crash ingestion or the Telegram bot.

### REQ-015 — Historical analog retrieval and impact projection

**Statement:** Every emitted intelligence signal must estimate likely BTC impact using historical analogs rather than a free-form narrative alone.

**Launch design constraints:**

- Launch uses a curated historical event set rather than an unbounded raw archive.
- Each historical analog row stores the event text or summary, timestamp, category, embedding, and realized BTC returns over multiple forward windows.
- Retrieval filters by category match first, then semantic similarity.
- Regime-aware filtering is optional at launch and required only in a later phase-2 expansion.

**Expected behavior:**

- For each eligible event, retrieve top-K analogs from the curated set.
- Compute direction, magnitude band, and duration band from the analog return distribution.
- Persist the analog IDs, analog count, analog return summary, and expected half-life on the signal.
- Surface the duration band to the user explicitly as `hours`, `days`, or `weeks`.

**Acceptance criteria:**

- Every emitted signal includes cited analog IDs and at least one analog-summary statistic.
- `/why <signal_id>` can show the retrieved analog set used for the signal.
- Launch analog retrieval works with a curated backfill and does not require a massive pre-launch corpus.

### REQ-016 — Signal data model and lifecycle

**Statement:** The `signals` datastore contract must be explicit, queryable from Telegram, and stable enough that storage technology can evolve behind it.

**Required persisted fields per signal:**

| Field | Requirement |
|---|---|
| `id` | unique signal identifier |
| `story_key` | stable dedupe key for the underlying event story |
| `source` | primary source name |
| `kind` | `news`, `calendar`, `derivatives`, `onchain`, or `regime` |
| `severity` | `low`, `medium`, `high`, `critical` |
| `detected_at` | timestamp |
| `expires_at` | timestamp |
| `summary` | user-facing one-line summary |
| `payload_json` | structured analysis payload |
| `source_url` | canonical primary URL when applicable |
| `analysis_status` | `fast`, `slow`, or `degraded` |

**Required `payload_json` content:**

- category
- direction
- magnitude band and score
- confidence
- horizon band
- expected half-life seconds
- novelty
- BTC specificity
- surprise when available
- analog IDs and analog summary statistics
- model versions
- source attribution list

**Acceptance criteria:**

- `/signals` can query active and recent signals without recomputing analysis.
- Signal rows include category, direction, magnitude, confidence, horizon, analog references, model versions, and source attribution.
- Signal persistence stays behind repository interfaces so a future vector-store migration does not change bot, rules, or monitor modules.

### REQ-017 — Telegram intelligence UX and `/signals` / `/why` behavior

**Statement:** Intelligence output must be understandable on Telegram, distinct from enforcement alerts, and auditable by the user.

**Expected behavior:**

- `/signals` replaces the v1 stub with real signal output.
- Signal summaries show severity, category, direction, magnitude band, horizon band, confidence, short summary, and attribution.
- `/why <signal_id>` shows the longer explanation, cited source URL(s), analog IDs, analog summaries, and model-version metadata.
- The first line of an intelligence push or `/why` detail must make it clear this is market intelligence, not a breach alert.
- The last line must make it clear that the message is informational and does not change discipline rules.

**Acceptance criteria:**

- Intelligence messages never reuse the deterministic breach-alert headline or cadence.
- Every `/why` response contains at least one direct attribution reference when a signal was source-driven.
- No intelligence message instructs the user to take a trade action.

### REQ-018 — Intelligence notification throttling and severity policy

**Statement:** Telegram intelligence pushes must be useful, bounded, and exposure-aware.

**Expected behavior:**

- Severity is one of `low`, `medium`, `high`, `critical`.
- `critical` signals push immediately.
- `high` signals push immediately only when at least one trade is open; otherwise they are stored for `/signals`.
- `medium` signals do not push immediately except scheduled macro reminders when open trades exist.
- `low` signals are stored only.
- No more than one push is allowed per `story_key`.
- No more than one push per category is allowed within the configured category cooldown window unless a newer signal upgrades severity to `critical`.

**Acceptance criteria:**

- Duplicate stories do not produce duplicate pushes.
- Intelligence pushes are deterministic under a frozen clock and a fixed open-trade state.
- Suppression rules do not prevent `/signals` from showing stored non-expired items.

### REQ-019 — Intelligence configuration, provider control, and budget limits

**Statement:** Intelligence-specific configuration must be explicit, validated at startup, and safe to operate on a small self-hosted deployment.

**Required configuration categories:**

- source enable flags for aggregator, RSS, macro calendar, Binance derivatives, later-phase X, and later-phase premium feeds;
- provider settings for the launch aggregator adapter, embedding model, and LLM model;
- analog retrieval settings, including top-K and curated-backfill location;
- notification throttle settings;
- daily LLM budget cap and hourly slow-lane call cap.

**Required behaviors:**

- If the daily slow-lane budget is exceeded, the system opens a circuit breaker that disables new slow-lane calls until reset, while keeping raw ingestion, fast-lane classification, and v1 behavior intact.
- If a required provider secret is missing for an enabled source or model client, startup fails clearly.
- If a later-phase source is disabled, the rest of intelligence still operates.

**Acceptance criteria:**

- Invalid intelligence config fails startup with a clear message.
- Budget-circuit state is visible in `/health`.
- Budget exhaustion never blocks `/new`, `/closed`, breach detection, or other v1 discipline commands.

### REQ-020 — Guardrails, evaluation, and monitoring

**Statement:** The intelligence layer must be auditable, budget-aware, and evaluated against historical replay before it is trusted in production.

**Required guardrails:**

- scraped or aggregated content is treated as untrusted text;
- slow-lane output is forced through structured JSON validation;
- analog citations must come only from retrieved analog IDs, not free-form model invention;
- intelligence output must be descriptive, not prescriptive;
- every signal must store classifier, embedding, and slow-lane model versions when used;
- daily model cost and source-health metrics must be persisted.

**Required evaluation:**

- a historical replay harness on a held-out window;
- direction-accuracy measurement;
- confidence calibration measurement;
- horizon-band accuracy measurement;
- a user-facing noise or usefulness summary sufficient to judge whether notifications are worth keeping enabled.

**Acceptance criteria:**

- The replay harness can run with frozen time and without look-ahead leakage.
- Every production signal stores model-version metadata.
- Signals that fail policy checks are rejected or downgraded rather than pushed.

### REQ-021 — Failure handling and degraded-mode behavior

**Statement:** Intelligence failures must degrade gracefully and must never weaken v1 discipline behavior.

**Expected behavior:**

- Source-adapter failures mark the source unhealthy, log the failure, and continue other sources.
- LLM timeouts, parse failures, or policy failures may produce a degraded fast-lane-only signal or no signal, depending on configured severity policy.
- Embedding or vector-search failures mark analog retrieval unavailable and prevent slow-lane output from claiming analog-backed confidence.
- `/health` exposes intelligence enabled state, source freshness, last successful signal time, budget-circuit status, and degraded-mode status.
- All intelligence failures are informational failures only. They must not block trade creation, breach detection, breach escalation, or discipline reporting.

**Acceptance criteria:**

- Simulated source, embedding, and LLM failures do not stop v1 flows.
- `/health` reflects degraded intelligence state within one polling interval.
- A degraded signal is clearly marked in stored data and Telegram rendering.

## 7. Non-Functional Requirements

- **Performance:** breach-detection and breach-alert latency requirements from v1 remain unchanged. Launch intelligence processing must normalize launch-source items within 120 seconds of adapter retrieval and render `/signals` / `/why` from stored data in under 2 seconds.
- **Security:** chat ID whitelist remains mandatory. Provider secrets are never logged. Source text is treated as untrusted input. Slow-lane calls use structured-output validation and prompt-injection defenses.
- **Privacy:** single-user, self-hosted. No mandatory telemetry beyond configured source and model providers.
- **Reliability:** intelligence may fail open in an informational sense, but v1 discipline enforcement must continue. Redis persistence prevents data loss across restarts. Source outages are visible in `/health`.
- **Cost control:** launch fast lane must run locally or at bounded fixed cost. Slow-lane and embedding costs must respect a daily budget cap and circuit breaker.
- **Scalability:** still out of scope beyond one user and one BTC symbol.
- **Maintainability:** source adapters, embedding clients, and model clients are interface-based and independently testable. Deterministic projection logic is separated from LLM explanation logic.
- **Observability:** structured logs include stable intelligence events such as `source_polled`, `event_deduped`, `signal_emitted`, `slow_lane_skipped`, and `budget_circuit_open`.

## 8. UX Requirements

### 8.1 Happy-path entry flow

The phase-1 entry flow remains unchanged and must continue to behave exactly as documented in v1.

### 8.2 `/signals` summary flow

```text
User: /signals
Bot:  ℹ️ Market intelligence
      #184 high regulatory_adverse | bearish | days | conf 0.71
      SEC filing signals delayed approval timetable; similar events saw median -2.2% at 24h.
      Source: CoinDesk + SEC EDGAR
      #183 medium funding_oi_extreme | bearish | hours | conf 0.64
      Funding elevated across Binance OI expansion; reversal risk rising.
      Informational only — discipline rules unchanged. Use /why <id> for detail.
```

### 8.3 `/why` detail flow

```text
User: /why 184
Bot:  ℹ️ Market intelligence #184
      Category: regulatory_adverse
      Bias: bearish | Magnitude: high | Horizon: days | Confidence: 0.71
      Why: SEC filing changed approval timing expectations and matched prior adverse regulatory delay events.
      Analogs: #114, #142, #203
      Median BTC return: -0.9% at 4h, -2.2% at 24h
      Source: https://...
      Informational only — this does not change any discipline rule.
```

### 8.4 Intelligence push style

- Breach alerts keep the aggressive `🚨 INVALIDATION BREACHED` style.
- Intelligence pushes use `ℹ️` or `⚠️ Market intelligence` and never use the breach-alert headline.
- Intelligence pushes must be shorter than breach-alert escalations and must not repeat on the same cadence.

## 9. Business Rules

- **BR-1** Only one in-progress commitment form at a time.
- **BR-2** A trade in `OPEN_OVERRIDE` counts as a rule violation in adherence stats regardless of final P&L.
- **BR-3** Leverage block and size-reduction policy are config-tunable but cannot be disabled.
- **BR-4** Only the whitelisted chat ID can issue commands.
- **BR-5** Invalidation cannot be edited after a trade is `OPEN`.
- **BR-6** Intelligence signals never modify trade, breach, alert, or conversation state.
- **BR-7** Intelligence messages must remain visually distinct from breach and monitor-health alerts.
- **BR-8** One market story may have many source records but only one active `story_key` for notification purposes.
- **BR-9** Slow-lane output that includes trade-action language is rejected or downgraded before storage or notification.

## 10. Data Requirements

**Inputs:**

- Telegram updates
- BTCUSDT price ticks from exchange websocket
- launch intelligence source records from aggregator, RSS, macro calendar, and Binance derivatives snapshots

**Stored entities:**

- `trades` — one record per committed trade plus indexes
- `breaches` — one record per breach event plus indexes
- `alerts` — one record per breach alert plus indexes
- `conversation_state` — form state keyed by chat ID
- `signals` — one record per emitted intelligence signal plus indexes
- `raw intelligence events` — normalized source records used for attribution and replay
- `historical analog records` — curated analog dataset with embeddings and realized return windows
- `source-health and budget state` — operational intelligence metadata

**Derived data:**

- adherence metrics
- intelligence severity
- novelty score
- source quality score
- analog-based return distributions

**Retention:** indefinite unless the user explicitly prunes data. Historical analog and raw-event retention must be sufficient to support replay, `/why`, and debugging.

## 11. Integrations

- **Telegram Bot API** — command surface and alerts
- **Binance USDT-M Futures WebSocket** — price monitor
- **Redis / Redis Stack** — authoritative datastore and launch vector search
- **Aggregator API adapter** — launch news source
- **Direct RSS / first-party feeds** — launch news source
- **Macro calendar adapter** — scheduled-event source
- **Embedding provider** — launch analog retrieval support
- **LLM provider** — slow-lane structured explanation and classification refinement

Later-phase optional integrations:

- curated X/Twitter-alternative source
- premium on-chain and derivatives vendors

## 12. Assumptions

- **A1** The user continues to open and close trades manually on their exchange.
- **A2** Telegram remains the only user-facing interface in phase 2.
- **A3** Binance remains the launch price source for discipline monitoring.
- **A4** The project remains single-user and self-hosted.
- **A5** BTC-only scope continues through phase 2.
- **A6** Launch analog retrieval uses a curated historical dataset, not a massive scraped archive.
- **A7** Launch intelligence uses a conservative source set and keeps expensive social or premium data optional.
- **A8** Slow-lane model calls are optional enhancements; the system must still function in a bounded degraded mode without them.
- **A9** The intelligence layer is valuable only if it stays clearly non-binding.

## 13. Open Questions

- **OQ-1** Which curated X/Twitter-alternative provider should be adopted first in the later expansion phase? This does not block launch because social ingestion is explicitly deferred.
- **OQ-2** Which premium on-chain or ETF-flow provider, if any, is worth the later operational cost for a single-user deployment? This does not block launch because launch scope intentionally excludes mandatory premium dependencies.
