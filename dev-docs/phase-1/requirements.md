# Requirements: BTC Discipline Bot

## 1. Overview

The **BTC Discipline Bot** is a personal behavioral commitment tool for a discretionary BTC perpetual futures trader. It does **not** generate signals, place orders, or advise on direction. It acts as a friction layer that enforces the user's own pre-committed trading rules at the two moments where discretionary traders self-destruct:

1. **Entry** — by requiring a full pre-trade checklist (direction, size, leverage, invalidation, max loss, regime, thesis) before a trade can be marked "open."
2. **Exit** — by monitoring BTC price via exchange websocket and aggressively pinging the user when their stated invalidation level is breached and the position is still open.

The system logs every commitment and every adherence/violation, producing a longitudinal dataset that lets the user diagnose their real discipline patterns: how often they raise leverage in losing streaks, how often they ignore their own invalidation, how often they trade against their stated regime.

- **Who it is for:** a single discretionary BTC trader (the user) whose P&L losses come from execution and discipline failures, not analytical errors.
- **Problem solved:** the user's reads on direction are good; the user's execution under emotional pressure is poor. Exchange stop-losses are inadequate because the user cancels them or trades without them. This system adds a human-in-the-loop friction layer that the user cannot quietly remove.

## 2. Goals

- **G1** Make it impossible to mark a trade "opened" without a complete pre-trade checklist.
- **G2** Default-block leverage ≥ 20X; require explicit override with a typed reason.
- **G3** After 2 consecutive losing closed trades, force the next trade's size to be ≤ 50% of the most recent winning trade's size.
- **G4** Detect invalidation breach within 5 seconds of the breach price printing on the exchange feed.
- **G5** Deliver an aggressive alert (Telegram message with notification) within 10 seconds of an invalidation breach.
- **G6** Require the user to either (a) mark the trade closed or (b) write a free-text justification when their invalidation has been breached.
- **G7** Persist every commitment, every breach, and every user response so weekly/monthly rule-adherence statistics can be computed.
- **G8** Single-user, low-cost, self-hostable on a small VPS or laptop.

## 3. Non-Goals

- **NG1** Not a trading bot. Does not connect to an exchange to place, modify, or cancel orders.
- **NG2** No market analysis, predictions, signals, or directional advice.
- **NG3** Single instrument in v1 (BTCUSDT perp only).
- **NG4** Single user, single chat. No multi-tenant, no roles.
- **NG5** Does not auto-close positions on breach; the user closes manually.
- **NG6** No native mobile app; Telegram is the interface.
- **NG7** No web dashboard in v1; logs are queryable via Telegram commands and direct Redis inspection with `redis-cli`.
- **NG8** No tax reporting, no P&L attribution to entries, no accounting.
- **NG9** No regime dashboard (ETF flow, funding) in v1 — flagged as v2 candidate per source spec.
- **NG10** No AI / LLM / intelligence layer behavior in v1. News capture, market-conditions analysis, regime classification, and any LLM-driven analysis are v2 features. v1 ships only the architectural seams (event bus, signals table, rule-context object, intelligence module stub, `/signals` command stub) so v2 can be added without modifying v1 modules. See REQ-010.

## 4. Users and Use Cases

**Primary user:** a single discretionary crypto trader.

- As a discretionary trader, I want to be **forced** to fill in my invalidation price before I can mark a trade open, so I cannot enter a position without a pre-committed exit plan.
- As a discretionary trader, I want the bot to **block 20X leverage** by default, so my "I'll just use a bit more leverage this once" impulse meets friction.
- As a discretionary trader, I want the bot to **enforce size reduction after consecutive losses**, so revenge-sizing into a bigger loser is structurally prevented.
- As a discretionary trader, I want to be **pinged aggressively when my invalidation is breached**, so I cannot quietly hold a loser past my own stated limit.
- As a discretionary trader, I want to be **forced to write a justification** if I refuse to close at my invalidation, so breaking my own rule has a friction cost and creates a record.
- As a discretionary trader, I want a **weekly report** of rule-adherence rate, so I can measure whether the tool is actually changing my behavior.

## 5. Functional Requirements

### REQ-001 — Pre-trade commitment form

**Statement:** Before a trade can transition to status `OPEN`, the user must complete a structured commitment form via a Telegram conversation. All fields are validated before the trade is created.

**Required fields:**
| Field | Type | Constraints |
|---|---|---|
| direction | enum | `long` or `short` |
| size_usdt | number | > 0, notional |
| leverage | integer | 1–125 (subject to REQ-002) |
| entry_price | number | > 0 |
| invalidation_price | number | > 0, must be < entry for long, > entry for short |
| max_loss_usdt | number | > 0 |
| regime | enum | `uptrend`, `range`, `downtrend`, `event_risk` |
| thesis | string | 10–280 chars |

**Rationale:** Forces the user to articulate the full plan before commitment, preventing impulsive entries lacking an exit plan.

**Expected behavior:** The bot walks the user through fields sequentially. Each field is validated before the next prompt. Once all fields pass validation **and** discipline rules (REQ-002, REQ-003), the trade transitions to `OPEN` and the price monitor immediately registers the invalidation level.

**Edge cases:**
- User abandons form mid-flow → expire after 10 minutes idle; no trade created.
- Invalidation on wrong side of entry → reject with explanation, re-prompt same field.
- Multiple simultaneously-open trades → allowed; each monitored independently. Warn user at second open: "You already have an open trade."

**Acceptance criteria:**
- A trade with status `OPEN` cannot exist in the DB without all 8 fields populated and validated.
- Validation errors produce clear messages naming the failing field and the rule.
- Monitor subscription is registered within 1s of `OPEN` transition.

### REQ-002 — Leverage block

**Statement:** Leverage values ≥ 20X are blocked by default. To proceed, the user must type a one-line reason (≥ 10 chars) explaining why high leverage is justified for this specific trade. The reason is persisted.

**Rationale:** From the user's loss review: 20X was the source of catastrophic P&L damage.

**Expected behavior:** When leverage ≥ 20X is entered, the bot responds with a fixed warning ("You are about to use ≥20X leverage. This is the leverage level that historically destroyed your account. Type a one-line reason to proceed, or /cancel to lower it.") and waits for either the reason or `/cancel`.

**Edge cases:**
- Leverage > 125 → reject (exchange max).
- Non-integer or non-positive → reject.
- `/cancel` → return to leverage prompt; trade not created.
- Threshold itself (20) → blocked. Strict `>=` comparison.

**Acceptance criteria:**
- No trade with leverage ≥ threshold can be created without a non-empty `leverage_override_reason`.
- Override reasons are visible in `/stats` output.

### REQ-003 — Consecutive-loss size reduction

**Statement:** After 2 consecutive closed trades with realized P&L < 0, the next trade must use `size_usdt` ≤ 50% of the size of the most recent winning trade. If no winning trade exists, the cap is 50% of the average of the last 5 trades (or 50% of the max size ever used, whichever is smaller).

**Rationale:** From the loss review: revenge-sizing into losing streaks.

**Expected behavior:** During form size entry, the rule engine queries trade history. If the streak ≥ threshold, compute cap. If entered size > cap, reject with explanation ("You are on a 2-loss streak. Max size for this trade is X USDT (50% of your last winning trade). Re-enter."). A winning trade resets the streak.

**Edge cases:**
- Break-even trade (P&L = 0): does **not** reset, does **not** increment.
- Fewer than 5 prior trades and no winners: cap = 50% of max size used so far.
- No prior trades at all: no cap.
- Manual P&L override (`/setpnl`) recomputes the streak.

**Acceptance criteria:**
- Trade creation rejected if size > cap when streak ≥ threshold.
- Enforcement is logged on the trade record (`size_reduction_enforced=1`).
- `/streak` command shows current streak and active cap.

### REQ-004 — Active price monitor

**Statement:** While any trade is in status `OPEN` or `OPEN_OVERRIDE`, the system maintains a websocket subscription to BTCUSDT perpetual price ticks on the configured exchange. On each tick, every open trade's invalidation is evaluated.

**Rationale:** The user holds losers past invalidation; an automated fast detector is required.

**Expected behavior:**
- Subscribe on service start.
- For each tick, for each open trade: long breach iff tick ≤ invalidation; short breach iff tick ≥ invalidation.
- First breach for a trade fires the breach handler (REQ-005). Until the user responds, additional ticks in the same direction do **not** create new breach records (de-dup by `armed` flag).
- After user response (`/closed` or `/justify`), arming resets. A subsequent breach (price recovers then breaches again) re-fires.

**Edge cases:**
- Websocket disconnect → exponential backoff reconnect (1, 2, 4, 8, 16, 32, 60s, then steady 60s). User notification is tiered by exposure — see REQ-009.
- Stale tick (timestamp > 30s old) → discard and force reconnect; treated as a disconnect for REQ-009 purposes.
- Price gap through invalidation between ticks → first tick on the wrong side counts as breach.
- Coverage gap > 60s → on the first tick after reconnect, re-evaluate every open trade so gap-through breaches that happened while offline are caught immediately.

**Acceptance criteria:**
- Breach detected within 5s of the breach tick arriving from the exchange (NFR perf).
- Disconnections logged; gaps > 60s flagged in `/health`.

### REQ-005 — Breach alert and forced response

**Statement:** When a breach is detected, the bot sends an aggressive Telegram alert and requires `/closed <price>` or `/justify <reason>` from the user. If neither arrives, alerts escalate.

**Expected behavior:**
- **First alert** (immediate): "🚨 INVALIDATION BREACHED. Trade #{id} ({direction} @ {entry}). You said you would exit at {invalidation}. Price is {current}. Close now or write a justification."
- **Escalation:** if no response, re-send every 60s for the first 5 minutes, then every 5 minutes thereafter. Each re-send includes elapsed time and current loss vs. max loss.
- `/closed <fill_price>` → trade → `CLOSED`, record `closed_at`, `close_price`, compute `realized_pnl`, log adherence. Stop escalation.
- `/justify <reason>` → trade → `OPEN_OVERRIDE`, persist justification text, stop escalation but resume monitoring. A subsequent breach re-arms alerts.

**Edge cases:**
- Price recovers above invalidation before user responds → alerts continue. The breach already happened.
- User starts a different form mid-breach (`/new`) → form is allowed; `/closed` and `/justify` still work and take precedence.
- User closes on exchange but never tells the bot → bot keeps alerting until `/closed` is sent. This is by design.

**Acceptance criteria:**
- One breach record per breach event; user response linked to it.
- Escalation cadence is unit-test verifiable with a frozen clock.

### REQ-006 — Trade lifecycle commands

**Statement:** The bot supports these Telegram commands:

| Command | Purpose |
|---|---|
| `/new` | Start commitment form |
| `/closed <price>` or `/closed <id> <price>` | Close most recent (or specific) open trade |
| `/justify <reason>` | Submit breach justification |
| `/cancel` | Cancel in-progress form |
| `/open` | List currently open trades |
| `/streak` | Show consecutive-loss counter and active size cap |
| `/stats [days]` | Adherence + P&L stats over last N days (default 30) |
| `/setpnl <trade_id> <pnl>` | Manual P&L override (for fees/partial fills) |
| `/health` | WS status, last tick age, open trade count |
| `/help [cmd]` | Command list or per-command help |

**Acceptance criteria:**
- All commands respond < 2s under normal load.
- Each command has a help string accessible via `/help <command>`.

### REQ-007 — Adherence stats

**Statement:** The `/stats` command and the weekly summary deliver the following metrics over a rolling window:
- Total trades, wins, losses, breakeven.
- Win rate, total realized P&L.
- Breach count, **adherence rate** = `(breaches resolved by /closed at or better than invalidation) / total breaches`.
- Leverage override count.
- Size-reduction enforcement count and compliance rate.
- P&L broken down by stated regime.

**Acceptance criteria:**
- Stats are deterministic on the same input.
- Weekly summary is pushed every Monday 09:00 in the user's configured timezone.

### REQ-008 — Configuration

**Statement:** The following parameters are configurable at startup via env vars / `.env`:

| Var | Default |
|---|---|
| `TELEGRAM_BOT_TOKEN` | required |
| `TELEGRAM_CHAT_ID` | required (whitelist) |
| `EXCHANGE` | `binance` (also: `bybit`) |
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

**Acceptance criteria:**
- Invalid values fail startup with a clear error.
- Changing config requires restart only (no runtime UI in v1).

### REQ-009 — Monitor health notifications

**Statement:** When the price monitor cannot evaluate breaches (websocket disconnected, ticks stale, or bot process crashed), the user is notified via Telegram. Notification timing is tiered by exposure so transient network blips do not generate noise, but real outages reach the user fast when positions are at risk.

**Rationale:** The entire purpose of the system is sub-5-second breach detection. A silent monitor is the worst failure mode — the user thinks they are protected when they are not. The original "alert after 5 minutes" was too slow.

**Tier table:**

| State | Open trades? | First alert delay | Repeat interval |
|---|---|---|---|
| WS disconnect or stale ticks | Yes | 10s | every 60s while down |
| WS disconnect or stale ticks | No | 60s | every 5min while down |
| Reconnect after previous alert | Either | recovery message immediately | n/a |
| Daily heartbeat (healthy) | Either | fires at `HEARTBEAT_TIME_LOCAL` | once per day |
| Daily heartbeat (unhealthy) | Either | suppressed (down-alert already running) | n/a |

**Alert content:**
- First alert (open trades): `"🚨 Price feed offline for {Xs}. You have {N} open trade(s) — invalidation monitoring is OFFLINE. Watch price manually until reconnected."`
- Repeat: same template with updated duration and reconnect-attempt count.
- Recovery: `"✅ Price feed back online after {Xs}. Monitoring resumed. {Coverage-gap warning if applicable.}"`
- Daily heartbeat (healthy): `"🟢 Daily check: WS connected, last tick {age}s ago, {N} open trade(s)."`

**Dead man's switch:** the daily heartbeat is the user's signal that the entire process is alive. If the user does not receive it at the expected time, they should assume process-level failure and check the host. This is documented in the runbook.

**Process-level failure handling:** the bot cannot self-notify if its own process is dead. Mitigations are operational:
- Docker Compose service with `restart: always` — covered by deployment task. A systemd unit may still be provided as optional host-level supervision for the Compose stack.
- Optional external uptime monitor (e.g., uptime-kuma, healthchecks.io) pinging a heartbeat file written each minute by the bot. If the file goes stale, the external monitor alerts the user via its own channel.
- Both are documented in `RUNBOOK.md`; the external monitor is recommended but not required.

**Edge cases:**
- Brief disconnect (< 10s) with open trades → no alert.
- Reconnect within the alert window then immediate re-disconnect → debounce window restarts; only sustained ≥ 10s gaps alert.
- Five disconnects within 10 minutes → flapping detected; the next disconnect alerts immediately regardless of duration. Logged at WARN.
- Telegram API itself is down → bot retries 3× per design §8; if still failing, ERROR-log and continue. Accepted limitation.
- Heartbeat scheduled time falls while the monitor is unhealthy → heartbeat suppressed; the active down-alert is sufficient.
- Coverage gap > 60s on reconnect → recovery message includes the gap duration AND immediately re-evaluates all open trades against the first post-reconnect tick.

**Acceptance criteria:**
- A simulated 15s disconnect with one open trade produces exactly one initial alert at ~10s and one recovery message on reconnect.
- A simulated 8s disconnect with one open trade produces no Telegram messages.
- A simulated 70s disconnect with no open trades produces one alert at ~60s and one recovery message.
- Daily heartbeat fires at the configured local time when healthy; is suppressed when an active down-alert exists.
- Five 8s disconnects in 10 minutes cause the 6th disconnect (even if brief) to alert immediately.
- Reconnect after a > 60s gap re-evaluates open trades on the first new tick.

### REQ-010 — AI-agent extension surface (stubs only in v1)

**Statement:** v1 exposes the minimum surface needed so a future intelligence layer — real-time news capture and analysis, market-conditions / regime classification, LLM-driven summarization — can be added in v2 without modifying any v1 module. v1 ships **only the stubs**; no intelligence behavior is implemented.

**In scope for v1 (stubs):**

| Surface | v1 behavior |
|---|---|
| `signals` Redis key namespace | Schema/key contract created; empty; no writers in v1. Reserved for v2 intelligence module. |
| `RuleContext` object | Passed to every rule function. `signals` field is always an empty mapping in v1. Rule decisions are unchanged by it. |
| Internal asyncio event bus | Implemented. v1 publishes `tick`, `breach_detected`, `breach_resolved`, `trade_opened`, `trade_closed`, `monitor_down`, `monitor_recovered`. v1 has no v1-only subscribers beyond what the existing modules need; future intelligence modules subscribe. |
| `/signals` Telegram command | Implemented as a stub. Replies: "Intelligence layer not configured. v2 feature — see REQ-010." |
| `src/intelligence/` module | Exists as an empty Python package with a docstring declaring the v2 boundary and the read-only constraint. No runtime imports of it from `src/`. |

**Hard architectural constraint (binding on v2 implementations):**
- The intelligence module, when implemented in v2, MUST be **read-only** with respect to trades, rules, alerts, and execution decisions.
- It MAY write to `signals` and MAY publish events on the bus.
- It MUST NOT write to `trades`, `breaches`, `alerts`, or `conversation_state`.
- It MUST NOT influence whether a trade is opened, blocked, sized, or closed.
- It informs the user; it does not enforce.

**Rationale:** The deterministic discipline rules are the system's value proposition. Allowing AI-derived signals to modulate enforcement would reintroduce the exact failure mode v1 prevents — negotiable, hallucinatable, slow, and blame-shiftable rules. Codifying the read-only boundary architecturally (and not merely as a code comment) prevents drift in v2.

**Out of scope for v1:**
- Any news ingestion, RSS/X polling, or web scraping.
- Any LLM client implementation, prompt management, or token-budget tracking.
- Any regime classifier, funding-rate adapter, or ETF-flow adapter.
- Any signal storage beyond the empty table.
- Any UI for signals beyond the stub `/signals` reply.

**Acceptance criteria:**
- Cold-start with no intelligence module produces no errors; all of REQ-001 through REQ-009 work as specified.
- Every rule function accepts `RuleContext` and produces identical decisions whether `signals` is empty or populated with arbitrary data (proves no v1 rule reads from signals).
- `/signals` returns the documented stub message.
- The event bus publishes the seven documented v1 event types; a test subscriber receives them in the expected order for a scripted scenario.
- The `signals` Redis key namespace exists per key contract and remains empty in all v1 tests.
- A convention check (lint rule or test) verifies that no code path in `src/` outside `src/intelligence/` writes to `signals` or to `trades`/`breaches`/`alerts` from intelligence-namespaced code (the latter is a no-op in v1 since the module is empty, but the check is in place for v2).

### REQ-011 — Docker Compose runtime and Redis persistence

**Statement:** The project must run through Docker Compose and use Redis as the durable application datastore instead of SQLite. Redis data must persist across container restarts by mounting a host directory into the Redis container and enabling Redis persistence.

**Rationale:** The service should be reproducible to run on a VPS or laptop with a single `docker compose up -d` command, while retaining trade, breach, alert, conversation, and signal records after container recreation.

**Expected behavior:**
- The repository includes a `docker-compose.yml` that starts both the bot service and a Redis service.
- The bot service connects to Redis using `REDIS_URL`.
- Redis stores all committed trades, breaches, alerts, conversation state, schema/version metadata, and the reserved v2 `signals` data.
- Redis persistence is enabled with append-only file (AOF) persistence.
- Redis persists data to a mounted host directory, defaulting to `./data/redis` in local deployment.
- Restarting, stopping, or recreating containers does not delete committed bot data when the mounted host directory remains intact.

**Edge cases:**
- Redis unavailable at startup → bot fails startup with a clear error and does not accept Telegram commands.
- Redis connection lost during runtime → commands that require persistence fail with a short user-facing internal error; monitor and bot retry Redis connections with bounded backoff and log the outage.
- Redis data directory missing on host → Docker Compose creates it or startup documentation instructs the user to create it.
- Redis data directory is not writable → Redis container fails clearly; `/health` cannot report OK until persistence is available.
- Container recreated with the same mounted data directory → all previously committed records remain available.

**Acceptance criteria:**
- `docker compose up -d` starts Redis and the bot service without requiring a local Python or Redis install.
- Creating a trade, stopping the stack, and starting the stack again preserves the trade and shows it in `/open` or `/stats` as applicable.
- Redis is the only authoritative application datastore in v1; no SQLite database file is created or required.
- `docker-compose.yml` mounts a host directory into Redis for durable data and configures Redis AOF persistence.
- `/health` reports Redis connectivity and persistence status.

## 6. Non-Functional Requirements

- **Performance:** tick-to-breach decision < 100ms; Telegram command p95 < 2s.
- **Security:** chat ID whitelist; secrets never logged; Redis reachable only on the Docker network by default; Redis data directory permissions documented; all Redis access goes through repository methods with fixed key builders and no user-controlled command construction.
- **Privacy:** single-user, self-hosted. No telemetry to third parties.
- **Reliability:** automatic websocket reconnect; on process restart, all open trades resume monitoring; Redis persistence prevents data loss across container restarts; atomic Redis operations or Lua scripts prevent inconsistent state on crash.
- **Scalability:** out of scope — one user, one symbol.
- **Maintainability:** discipline rules isolated in pure `rules/` module with full unit-test coverage; exchange adapter is interface-based.
- **Observability:** structured JSON logs to stdout; stable event names (`trade_opened`, `breach_detected`, `alert_sent`, `ws_reconnect`); manual `/health` command.

## 7. UX Requirements

### Happy-path entry flow

```
User: /new
Bot:  Direction? (long/short)
User: long
Bot:  Size in USDT notional?
User: 5000
Bot:  Leverage?
User: 10
Bot:  Entry price?
User: 82500
Bot:  Invalidation price? (must be < entry for long)
User: 81200
Bot:  Max loss in USDT?
User: 160
Bot:  Regime? (uptrend / range / downtrend / event_risk)
User: uptrend
Bot:  One-line thesis (10–280 chars)?
User: Holding above $82K with strong ETF inflows, expecting continuation.
Bot:  ✅ Trade #47 committed and monitored.
      Invalidation: 81200. Max loss: 160 USDT. /open to view.
```

### Leverage-block flow

```
Bot:  Leverage?
User: 20
Bot:  ⚠️ You are about to use ≥20X leverage. This is the leverage level
      that historically destroyed your account. Type a one-line reason
      to proceed, or /cancel to lower it.
User: Tight stop, defined event, small size.
Bot:  Entry price?
```

### State expectations

- **Validation errors:** inline reject with field name, rule, and example. Re-prompt same field, no flow restart.
- **Empty states:** `/open` → "No open trades. Use /new to commit one." `/stats` with no data → "No closed trades in the last {n} days."
- **Loading states:** N/A (commands sub-second).
- **Success states:** confirmation always shows trade ID + key enforced parameters.
- **Error states:** name the problem + the fix.

## 8. Business Rules

- **BR-1** Only one in-progress commitment form at a time. Starting `/new` while a form is active prompts `/cancel` first.
- **BR-2** A trade in `OPEN_OVERRIDE` counts as a rule violation in adherence stats regardless of final P&L.
- **BR-3** Leverage block and size-reduction policy are config-tunable (thresholds, factor) but **cannot be disabled** in v1.
- **BR-4** Only the whitelisted chat ID can issue commands. Any other chat is silently ignored with a WARN log.
- **BR-5** Invalidation **cannot be edited** after a trade is `OPEN`. (Editing it would let the user widen the stop under pressure — the exact behavior the system prevents.)

## 9. Data Requirements

**Inputs:** Telegram updates; BTCUSDT price ticks from exchange websocket.

**Stored entities:**
- `trades` — one Redis hash per committed trade plus indexes; all 8 commitment fields + status + override fields + timestamps + realized P&L.
- `breaches` — one Redis hash per breach event plus indexes, linked to trade; includes user response and justification text.
- `alerts` — one Redis hash per alert sent plus indexes (audit + de-dup).
- `conversation_state` — Redis hash/string state keyed by chat ID.

**Derived data:** adherence rate, win rate, P&L totals, regime breakdowns. Recomputable from Redis `trades` + `breaches` records.

**Validation rules:** see REQ-001 through REQ-008.

**Retention:** indefinite. The longitudinal dataset is the point. Redis AOF data must be mounted to the host so retention is not tied to container lifetime.

## 10. Integrations

- **Telegram Bot API** — conversational interface and alerts.
- **Binance USDT-M Futures WebSocket** — public market data (`btcusdt@markPrice@1s` or `btcusdt@aggTrade`). No API key required.
- **Bybit WebSocket** (v1.1, additive via adapter) — same role.
- **Redis** — durable application datastore, run by Docker Compose with host-mounted persistence.

## 11. Assumptions

- **A1** The user opens and closes actual trades manually on their exchange. The bot does **not** place orders. The user self-reports `/closed` with the fill price.
- **A2** Telegram is the interface for v1 (source spec listed "Telegram or web"). Web UI is a v2 candidate.
- **A3** Binance is the price source for v1 (source spec listed "Binance/Bybit"). Bybit is an additive adapter in v1.1.
- **A4** Single-user, self-hosted. No multi-tenant concerns.
- **A5** BTC perp only in v1.
- **A6** "Consecutive losses" is defined by realized P&L on closed trades, not mark-to-market on open trades.
- **A7** Realized P&L is computed by the bot as `(close_price − entry_price) × size_in_BTC × direction_sign`; fees are not modeled in v1. User can override with `/setpnl`.
- **A8** Aggressive alerts use Telegram only. SMS / phone-call escalation is out of scope.
- **A9** The bot does **not** verify against the exchange that a position is actually open. Trust-based. The user has no rational incentive to deceive their own commitment device.
- **A10** Tech stack: Python 3.11+, asyncio, `python-telegram-bot` v20+, `websockets`, `redis.asyncio`, `pydantic` v2, `structlog`, `apscheduler`, Docker Compose, Redis.
- **A11** AI / intelligence layer is v2 only. v1 ships the extension seams (REQ-010) but no implementation. When v2 ships, high-volume telemetry from news/market sources may justify a separate Redis database number, Redis key namespace, or migration of signal telemetry to Postgres/TimescaleDB. v1 trade and breach data remains in Redis unless explicitly migrated.

- **A12** Docker Compose is the primary supported runtime for v1. Direct host execution may be used for development, but production-style operation uses the Compose stack.

## 12. Open Questions

- **OQ-1** Should the bot integrate with the exchange via API key to verify position existence and pull actual close prices? *Recommend: defer to v2; keep v1 trust-based to ship faster.*
- **OQ-2** Should there be a "time-stop" rule (auto-prompt to close any trade older than 24h)? *Not requested explicitly; flagged for the user.*
- **OQ-3** Should the bot allow editing invalidation after open? *Recommend: disallow in v1 (BR-5).*
- **OQ-4** Regime dashboard (ETF flow, funding rate, key levels) — source spec lists as "optional." *Recommend: defer to v2 unless prioritized.*
- **OQ-5** Should max-loss be enforced as an automatic close prompt distinct from invalidation? Currently treated as informational only. *Flag for user.*
- **OQ-6** What is the first intelligence source to ship in v2? *Candidates: BTC funding rate, ETF net-flow direction, FOMC/CPI event calendar, X/news sentiment. Recommend funding rate first — cheapest data, highest signal-to-noise.*
