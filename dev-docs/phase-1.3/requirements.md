# Requirements: Multi-Asset Support (Hyperliquid)

## Introduction

The BTC Discipline Bot currently monitors a single hard-coded instrument
(`BTCUSDT`) sourced from a Binance websocket. This feature generalizes the bot
to enforce the same pre-trade discipline and invalidation monitoring across
**every market Hyperliquid lists** — all crypto perps plus Hyperliquid's
equity, commodity (oil, gold), and forex (e.g. `AUDUSD`) perps — using
Hyperliquid as the sole price source. The bot remains a monitoring-only
discipline tool: it never places, modifies, or cancels orders, and the user
continues to self-report fills. The instrument becomes a per-trade field rather
than a global constant, and a single `allMids` websocket subscription supplies
mid prices for all open trades at once.

## Goals

- **G1** Allow the user to open a discipline-tracked trade on any symbol listed in Hyperliquid's perpetuals universe (crypto, equities, oil, gold, forex).
- **G2** Validate the chosen symbol against Hyperliquid's live market list at trade-entry time, rejecting unknown or mistyped symbols before a trade is committed.
- **G3** Monitor every open trade's invalidation against its own symbol's live Hyperliquid mid price, using one shared `allMids` websocket subscription regardless of how many distinct symbols are open.
- **G4** Apply the existing deterministic discipline rules — leverage block, consecutive-loss size reduction, full pre-trade checklist, breach escalation — uniformly across all symbols and asset classes.
- **G5** Lower the leverage block threshold default to `10` (was `20`) and apply it uniformly to every symbol.
- **G6** Preserve all existing v1/v2 behaviors (form flow, breach alerts, monitor-health tiering, stats, weekly report, `/edit`, `/edit_closed`, `/setpnl`, intelligence seam) except where this feature explicitly revises them.
- **G7** Remain a single-user, self-hosted, Telegram-first, monitoring-only tool sourcing data exclusively from Hyperliquid public endpoints.

## Non-Goals

- **NG1** No order placement, modification, cancellation, or any private/authenticated Hyperliquid endpoint use. Monitoring only; the user self-reports fills.
- **NG2** No spot-market support in this phase. Only Hyperliquid **perpetuals** (the `meta` universe) are in scope. Spot symbols (`PURR/USDC`, `@{index}`) are out of scope.
- **NG3** No migration of existing v1 `BTCUSDT` data. This is a clean break; existing trade/breach/alert/conversation data may be wiped on cutover.
- **NG4** No multi-exchange support. Binance and Bybit adapters are removed, not retained as stubs.
- **NG5** No per-asset-class rule customization. The leverage block and size-reduction rules apply identically to every symbol (G4).
- **NG6** No market-hours / session handling. Hyperliquid trades 24/7, so the bot treats every symbol as continuously monitorable.
- **NG7** No change to the read-only intelligence boundary (REQ-010 from prior phases). Intelligence remains out of the enforcement path.

## Glossary

- **System** — the Telegram bot (BTC Discipline Bot), now multi-asset.
- **Symbol** — a Hyperliquid perpetual market name as returned in the `meta` universe (e.g. `BTC`, `ETH`, `HYPE`, `SOL`, and Hyperliquid's equity/commodity/forex perp names). Case-normalized to Hyperliquid's canonical form.
- **Universe** — the set of valid perpetual symbols returned by Hyperliquid's `info` endpoint with `{"type":"meta"}`.
- **Mid price** — the mid price for a symbol as delivered by the `allMids` websocket subscription / `info` `allMids` query.
- **Open trade** — a trade in `OPEN` or `OPEN_OVERRIDE` status.
- **Invalidation** — the user-committed price level that, when breached, triggers escalation.

## Requirements

### Requirement 1: Per-trade symbol selection

**User story:** As a trader, I want to choose which Hyperliquid market a trade is on when I open it, so that I can track discipline across all the assets I trade rather than only BTC.

**Acceptance criteria:**
1. WHEN the user starts the `/new` form THEN the system SHALL prompt for a symbol as the first committed field, before direction.
2. WHEN the user submits a symbol THEN the system SHALL normalize it to Hyperliquid's canonical perpetual symbol form (e.g. uppercase) before validation.
3. IF the submitted symbol is present in the Hyperliquid perpetuals universe THEN the system SHALL accept it and advance the form to the next field.
4. IF the submitted symbol is not present in the universe THEN the system SHALL reject it with an error naming the field and re-prompt for the symbol without advancing.
5. WHEN a trade is committed THEN the system SHALL persist its symbol as an immutable field on the trade record.
6. THE system SHALL allow multiple simultaneously open trades on different symbols, each monitored independently.
7. THE system SHALL allow multiple simultaneously open trades on the same symbol, each monitored independently.

### Requirement 2: Symbol validation against the Hyperliquid universe

**User story:** As a trader, I want the bot to reject mistyped or unlisted symbols at entry, so that I never commit a trade the monitor cannot watch.

**Acceptance criteria:**
1. THE system SHALL retrieve the perpetuals universe from Hyperliquid's `info` endpoint using `{"type":"meta"}`.
2. THE system SHALL cache the retrieved universe and refresh it on a configurable interval so newly listed markets become selectable without a restart.
3. WHEN symbol validation is required AND the cached universe is older than the configured refresh interval THEN the system SHALL refresh the universe before validating.
4. IF the universe cannot be retrieved AND no cached universe is available THEN the system SHALL reject the symbol with a clear "market list unavailable, try again" message and SHALL NOT commit the trade.
5. IF the universe cannot be refreshed but a cached universe exists THEN the system SHALL validate against the cached universe and SHALL continue operating.
6. THE system SHALL treat symbol matching as exact against the canonical universe names after normalization (no fuzzy matching).

### Requirement 3: Multi-symbol price monitoring via `allMids`

**User story:** As a trader, I want every open position watched against its own market's live price, so that an invalidation breach on any symbol pings me regardless of which asset it is.

**Acceptance criteria:**
1. WHILE at least one trade is open THE system SHALL maintain a single websocket subscription to Hyperliquid `allMids` at `wss://api.hyperliquid.xyz/ws`.
2. WHEN an `allMids` update arrives THEN the system SHALL, for each open trade, evaluate that trade's invalidation against the mid price of the trade's symbol from the update.
3. IF an `allMids` update does not contain a mid price for an open trade's symbol THEN the system SHALL skip evaluation for that trade on that update and SHALL NOT raise a false breach.
4. WHEN a long trade's symbol mid price is at or below its invalidation price THEN the system SHALL treat it as a breach.
5. WHEN a short trade's symbol mid price is at or above its invalidation price THEN the system SHALL treat it as a breach.
6. THE system SHALL detect a breach within 5 seconds of the breaching mid price arriving from Hyperliquid.
7. WHILE no trades are open THE system MAY hold or drop the `allMids` subscription, and SHALL re-establish it when a trade is next opened.
8. THE system SHALL register monitoring for a newly opened trade within 1 second of the `OPEN` transition without requiring a new websocket connection per symbol.

### Requirement 4: Connection handling and reconnection

**User story:** As a trader, I want the price feed to recover automatically and tell me when it can't, so that I am never silently unprotected.

**Acceptance criteria:**
1. WHEN the Hyperliquid websocket disconnects THEN the system SHALL reconnect with exponential backoff and SHALL resubscribe to `allMids` on reconnect.
2. WHEN no `allMids` message has been received for longer than the configured staleness threshold THEN the system SHALL treat the feed as down and force a reconnect.
3. WHEN the feed has been down THEN the system SHALL apply the existing exposure-tiered monitor-down alerting (faster alerts when open trades exist).
4. WHEN the feed reconnects after a coverage gap greater than 60 seconds THEN the system SHALL re-evaluate every open trade against the first post-reconnect `allMids` update.
5. THE system SHALL surface Hyperliquid websocket status, last-update age, and last error in `/health`.

### Requirement 5: Uniform discipline rules across all symbols

**User story:** As a trader, I want my discipline rules enforced identically on every market, so that switching assets never weakens the friction layer.

**Acceptance criteria:**
1. THE system SHALL apply the full pre-trade checklist (symbol, direction, size, leverage, entry, invalidation, max loss, regime, thesis) to every trade regardless of symbol.
2. WHEN leverage entered for any symbol is greater than or equal to the leverage block threshold THEN the system SHALL require a leverage override reason before the trade can be committed.
3. THE system SHALL default the leverage block threshold to `10` and SHALL apply it uniformly to all symbols.
4. THE system SHALL compute the consecutive-loss size cap **per symbol**: the streak for a candidate trade SHALL count only that symbol's closed trades, and the size cap (when active) SHALL be derived from that symbol's most recent winning trade, falling back to that symbol's size history when no winner exists.
5. WHERE no prior closed trades exist for a given symbol THE system SHALL apply no size cap for that symbol regardless of streaks on other symbols.
6. THE `/streak` command SHALL report the streak and active cap **per symbol** for symbols with any closed history.
7. WHEN invalidation price is entered THEN the system SHALL validate it is on the correct side of entry for the trade's direction, identically for every symbol.
8. THE system SHALL continue to validate leverage within the supported range and SHALL NOT special-case any symbol or asset class.

### Requirement 6: Configuration changes

**User story:** As an operator, I want configuration to reflect the Hyperliquid-only, multi-asset model, so that no Binance/BTC assumptions remain.

**Acceptance criteria:**
1. THE system SHALL remove the global `SYMBOL` setting; the symbol is a per-trade field.
2. THE system SHALL set the exchange to Hyperliquid and SHALL remove the Binance and Bybit adapters entirely.
3. THE system SHALL default `LEVERAGE_BLOCK_THRESHOLD` to `10`.
4. THE system SHALL expose configuration for the Hyperliquid websocket URL, the `info` endpoint URL, the universe refresh interval, and the feed staleness threshold.
5. WHEN a required Hyperliquid configuration value is invalid THEN the system SHALL fail startup with a clear error.
6. THE system SHALL retain all unrelated existing configuration (alert cadence, monitor-down tiers, heartbeat, Redis, timezone) unchanged in meaning.

### Requirement 7: Display and command surface updates

**User story:** As a trader, I want every command that shows or edits a trade to show its symbol, so that I can tell my positions apart.

**Acceptance criteria:**
1. WHEN `/open` lists trades THEN the system SHALL display each trade's symbol.
2. WHEN a trade is committed THEN the confirmation message SHALL include the symbol.
3. WHEN a breach alert is sent THEN the alert SHALL identify the trade's symbol.
4. WHEN `/stats` reports P&L THEN the system SHALL continue to report deterministic discipline metrics, and the per-regime breakdown SHALL remain unchanged in meaning.
5. WHERE a trade-detail or edit confirmation message is shown THE system SHALL include the symbol.
6. THE `/closed`, `/justify`, `/setpnl`, `/edit`, and `/edit_closed` commands SHALL continue to identify trades by trade id, unchanged, and SHALL operate on any symbol.

### Requirement 8: Clean cutover from single-asset v1

**User story:** As an operator, I want a clean switch to the multi-asset model, so that no stale BTC-only data or assumptions linger.

**Acceptance criteria:**
1. THE system SHALL NOT require migration of existing `BTCUSDT` records; existing data MAY be wiped on cutover.
2. WHEN the system starts after cutover with an empty datastore THEN it SHALL operate normally with no open trades.
3. THE system SHALL NOT reference `BTCUSDT` or Binance as defaults anywhere in configuration, code paths, or user-facing copy after cutover.
4. THE system SHALL preserve the Redis schema/key contract for trades, breaches, alerts, and conversation state, extended only by the new per-trade `symbol` field.

## Non-Functional Requirements

- **Performance:** breach detection within 5s of the breaching mid price (R3.6); Telegram command responses under 2s.
- **Reliability:** automatic websocket reconnect with resubscription; open trades resume monitoring on restart; the single `allMids` subscription scales to many symbols without per-symbol connections.
- **Security:** single whitelisted chat ID; only public Hyperliquid endpoints; no API keys or signing; user text stored only as Redis values.
- **Maintainability:** Hyperliquid access behind the existing exchange-adapter interface; discipline rules remain pure and symbol-agnostic.
- **Observability:** `/health` reports Hyperliquid feed status; structured logs retain stable event names.
