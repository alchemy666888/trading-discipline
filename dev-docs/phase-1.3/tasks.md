# Tasks: Multi-Asset Support (Hyperliquid)

> Work through these in order. Check off each task only when its outcome is
> verified by the tests it lists or by the acceptance check named on it. If a
> task can't be completed as written, stop and flag it rather than improvising
> outside the spec. Every task cites the requirement(s) it satisfies; design
> details for each task live in `design.md`.
>
> Reference: `requirements.md` (R1–R8) and `design.md` (§1–§12) in this folder.
> The existing v1 codebase is the starting point. The `src/intelligence/`
> read-only boundary (REQ-010) must not be widened by any task in this plan.

## Phase 1 — Foundations: data shape and config

The whole change leans on three small but rippling contract updates: `Tick`
carries a symbol, `Trade` carries a symbol, and a new `ConversationStep`
exists. Do these first so every later task compiles against the new shape.

- [x] **1. Update the `Tick` contract to carry `symbol`.** _(R3.1, R3.2)_
  - [x] 1a. In `src/exchange/base.py`, add `symbol: str` to the `Tick` dataclass (first field, before `price`).
  - [x] 1b. Update the `ExchangeAdapter` ABC docstring to state that `stream_ticks()` may yield ticks for many symbols from a single subscription.
  - [x] 1c. Add a unit test that constructs a `Tick("BTC", 43250.5, ts)` and round-trips its `symbol` through the breach evaluator. _(Acceptance: `pytest tests/unit/test_tick_contract.py` green.)_

- [x] **2. Add `symbol` to the trade models and conversation state.** _(R1.1, R1.5, R7.2)_
  - [x] 2a. In `src/models/trade.py`, add a required `symbol: str` field on `Trade` (Pydantic v2) with a min-length validator. Add an optional `symbol: str | None` on `TradeDraft`.
  - [x] 2b. In `src/models/conversation.py`, add `ConversationStep.SYMBOL` as a new enum value. Update the step ordering used by the form orchestrator (see Task 11).
  - [x] 2c. Unit-test that a `Trade` instance round-trips through Redis (mocked or fake repo) preserving `symbol`, and that constructing a `Trade` without `symbol` raises. _(Acceptance: `pytest tests/unit/test_trade_model.py` green.)_

- [x] **3. Update configuration: remove single-symbol assumptions, add Hyperliquid settings.** _(R6.1, R6.2, R6.3, R6.4, R6.5)_
  - [x] 3a. In `src/config.py`, remove `SYMBOL` and `EXCHANGE` settings entirely. Change the `LEVERAGE_BLOCK_THRESHOLD` default from `20` to `10`.
  - [x] 3b. Add `HYPERLIQUID_WS_URL` (default `wss://api.hyperliquid.xyz/ws`), `HYPERLIQUID_INFO_URL` (default `https://api.hyperliquid.xyz/info`), `HYPERLIQUID_UNIVERSE_REFRESH_SECONDS` (default `300`), `HYPERLIQUID_UNIVERSE_STALE_SECONDS` (default `900`), `HYPERLIQUID_FEED_STALE_SECONDS` (default `30`), `HYPERLIQUID_FEED_REQUEST_TIMEOUT_SECONDS` (default `5`). All exposed as Pydantic-Settings env vars.
  - [x] 3c. Validate at startup: URLs must be non-empty strings; intervals must be positive ints. Fail-fast with a clear error if invalid.
  - [x] 3d. Update `.env.example` to match the new schema. _(Acceptance: `python -c "from src.config import settings; print(settings.HYPERLIQUID_WS_URL)"` succeeds on a fresh `.env` derived from the example.)_

## Phase 2 — Hyperliquid exchange adapter

- [x] **4. Implement `HyperliquidExchangeAdapter`.** _(R3.1, R3.2, R3.3, R3.4, R3.5, R4.1, R4.2, R4.3, R4.4)_
  - [x] 4a. Create `src/exchange/hyperliquid.py`. Class `HyperliquidExchangeAdapter(ExchangeAdapter)`.
  - [x] 4b. `stream_ticks()`: connect to the configured WS URL, send `{"method":"subscribe","subscription":{"type":"allMids"}}`, log the `subscriptionResponse` ack. For each `{"channel":"allMids","data":{"mids":{...}}}` frame, emit one `Tick(symbol, price, ts)` per symbol with `ts = datetime.utcnow()` (frame receive time — `allMids` carries no per-symbol timestamp).
  - [x] 4c. Apply the documented exponential backoff (1, 2, 4, 8, 16, 32, 60s steady). On reconnect, resubscribe and emit a `RECONNECTED` connection event carrying the gap duration. _(R4.1, R4.2)_
  - [x] 4d. If no frame arrives within `HYPERLIQUID_FEED_STALE_SECONDS`, force a reconnect and emit a `STALE` event. _(R4.3)_
  - [x] 4e. Skip (and log) frames where a mid is not parseable as `float` or where a `mids` entry is malformed — never emit a tick with a bad price. _(R3.5)_
  - [x] 4f. `healthy()` returns true iff the last frame arrived within `HYPERLIQUID_FEED_STALE_SECONDS`. `close()` cancels the WS task cleanly.
  - [x] 4g. Add `async def fetch_universe(self) -> list[str]`: POST `{"type":"meta"}` to the configured `info` URL with `Content-Type: application/json` and a `HYPERLIQUID_FEED_REQUEST_TIMEOUT_SECONDS` timeout. Parse `universe[*].name`, skipping entries flagged `isDelisted: true` if present. Return the list. Raise on HTTP error / timeout / malformed JSON. _(R2.2)_

- [x] **5. Adapter tests (mocked websocket + mocked HTTP).** _(R3.1–R3.5, R4.1–R4.4, R2.2)_
  - [x] 5a. Fake-WS test: one frame with several symbols → one tick per symbol, all carrying receive `ts`.
  - [x] 5b. Fake-WS test: a frame missing a previously-seen symbol → no tick for that symbol on that frame; no crash.
  - [x] 5c. Fake-WS test: a frame containing a non-numeric mid → that symbol skipped, log emitted, other symbols still emitted.
  - [x] 5d. Fake-WS test: simulated disconnect → backoff sleep observed (use freezegun + an injectable sleep), resubscribe sent on reconnect, `RECONNECTED` event published with non-zero gap.
  - [x] 5e. Fake-WS test: no frame for `HYPERLIQUID_FEED_STALE_SECONDS + 1` → `STALE` event, reconnect attempted.
  - [x] 5f. `fetch_universe` tests against a fake httpx response: returns canonical names; tolerates extra fields; raises on 4xx/5xx; raises on timeout; raises on malformed JSON. _(Acceptance: `pytest tests/adapter/test_hyperliquid.py` green; `src/exchange/hyperliquid.py` ≥85% line coverage.)_

- [x] **6. Delete `binance.py` and `bybit.py`; remove every import.** _(R6.2)_
  - [x] 6a. `git rm src/exchange/binance.py src/exchange/bybit.py`.
  - [x] 6b. Grep the tree for `binance`, `Binance`, `bybit`, `Bybit` references in `src/` and remove them. Tests that exercise those adapters are deleted in the same commit.
  - [x] 6c. Drop Binance/Bybit hostnames from any documented network-egress allowlist (README/RUNBOOK) — readded in Task 18.

## Phase 3 — Repository: per-symbol filter and universe cache

- [x] **7. Extend `RedisRepository` with optional `symbol` filter on closed-trade queries.** _(R5.4, R5.5, R5.6)_
  - [x] 7a. `list_closed_trades(symbol: str | None = None) -> list[Trade]`: when `symbol` is None, behavior is unchanged; when provided, filter to trades whose `symbol` matches (case-sensitive against the canonical stored form).
  - [x] 7b. `consecutive_loss_count(symbol: str | None = None) -> int`: same filter semantics.
  - [x] 7c. Integration tests (real Redis from the test fixture): seed an interleaved multi-symbol closed history; assert each symbol's `list_closed_trades` and `consecutive_loss_count` are isolated; assert the unfiltered call still returns all. _(Acceptance: `pytest tests/integration/test_repo_symbol_filter.py` green.)_

- [x] **8. Add the universe cache to `RedisRepository`.** _(R2.3, R2.4, R2.5)_
  - [x] 8a. In `src/db/keyspace.py`, add `hyperliquid_universe_key()` returning `hyperliquid:universe:perps`. Document in the keyspace file that this key is **outside** the trade/breach/alert/conversation/signals namespaces, so it does not affect the REQ-010 boundary.
  - [x] 8b. `get_universe() -> tuple[set[str], datetime] | None`: parse the stored JSON `{"symbols": [...], "fetched_at": "..."}`; return `(set, dt)` or `None` if missing.
  - [x] 8c. `set_universe(symbols: list[str], fetched_at: datetime) -> None`: atomic `SET` of the JSON document.
  - [x] 8d. Integration tests: set → get round-trip; absence → `None`; malformed value → `None` (logged). _(Acceptance: `pytest tests/integration/test_universe_cache.py` green.)_

## Phase 4 — Rules: validation and per-symbol scoping

- [x] **9. Implement `validate_symbol(value, universe)`.** _(R1.2, R1.3, R2.1)_
  - [x] 9a. In `src/rules/validation.py`, add `validate_symbol(value: object, universe: set[str]) -> str`. Reject non-strings and empty strings; trim and uppercase; require exact membership in `universe`; raise `ValueError` with a field-named message on mismatch (match the existing validator error style).
  - [x] 9b. Unit-test: canonical names accepted; `"  btc "` normalizes to `BTC`; unknown / empty / non-string rejected with the documented message. _(Acceptance: `pytest tests/unit/test_validate_symbol.py` green.)_

- [x] **10. Scope sizing and impact rules per symbol.** _(R5.4, R5.5, R5.6)_
  - [x] 10a. In `src/rules/sizing.py`, ensure `compute_size_cap` and `consecutive_loss_count` operate over the `ctx.recent_trades` list as-is — the orchestrator's job is to pass the symbol-scoped subset. Verify the rule math itself is unchanged; the only delta is what callers pass in. Add docstring noting the per-symbol caller contract.
  - [x] 10b. In `src/rules/impact.py`, change `discipline_impact(closed_trades, edited, ...)` callers to pass only the same-symbol subset for both the "before" and the "after" sets. The function itself can stay symbol-agnostic; the filter happens at the call site.
  - [x] 10c. Add unit tests proving: a streak on `BTC` does not cap an `ETH` candidate trade; a winning `BTC` trade does not reset an `ETH` streak; `discipline_impact` preview for an edited `BTC` close moves only the `BTC` before/after numbers. _(Acceptance: `pytest tests/unit/test_per_symbol_streak.py` green; `src/rules/` coverage stays ≥85%.)_

## Phase 5 — Bot: form, formatting, and commands

- [x] **11. Insert `SYMBOL` as the first form step with synchronous universe validation.** _(R1.1, R1.2, R1.3, R1.4, R2.1, R2.3, R2.4, R2.5)_
  - [x] 11a. In `src/bot/forms.py`, reorder the conversation state machine to: `IDLE → SYMBOL → DIRECTION → SIZE → LEVERAGE [→ LEV_OVERRIDE] → ENTRY → INVALIDATION → MAX_LOSS → REGIME → THESIS → CONFIRM → IDLE`. Update the handler dispatch table.
  - [x] 11b. On `SYMBOL` input: load the universe via `repo.get_universe()`. If absent or older than `HYPERLIQUID_UNIVERSE_STALE_SECONDS`, attempt a synchronous `adapter.fetch_universe()` and `repo.set_universe(...)` on success. If the fetch fails but a cache exists, validate against the cache and log `universe_refresh_failed`. If the fetch fails and no cache exists, reply with the "market list unavailable" copy (defined in Task 12) and stay on `SYMBOL` — no trade created. _(R2.4, R2.5)_
  - [x] 11c. On a validated symbol, write it into the `TradeDraft.symbol` and advance to `DIRECTION`. On invalid symbol, stay on `SYMBOL` and reply with the rejection copy.
  - [x] 11d. Ensure `/cancel` and the form-timeout reaper clear the in-progress draft as today.
  - [x] 11e. Bot-integration test (Redis + mocked Telegram + mocked adapter): happy-path `/new` from `SYMBOL` through `CONFIRM` commits a trade with the chosen symbol; typo on `SYMBOL` stays on `SYMBOL`; universe-unavailable-with-empty-cache returns the unavailable copy and stays on `SYMBOL`; stale-cache-accepted path succeeds. _(Acceptance: `pytest tests/integration/test_form_symbol_step.py` green.)_

- [x] **12. Centralize all symbol-aware user-facing copy in `src/bot/formatting.py`.** _(R7.1, R7.2, R7.3, R7.4, R7.5)_
  - [x] 12a. Add `prompt_symbol()`, `symbol_unknown(value)`, `symbol_universe_unavailable()`, and update `prompt_direction`, `prompt_size`, etc., so the prompts say "Symbol? (e.g. BTC, ETH, HYPE, AUDUSD)" and rejections name the field with the rule per the existing validator style.
  - [x] 12b. Update `format_trade_committed`, `format_open_trade`, `format_closed_trade`, `format_edit_result`, and the breach-alert formatter to include the trade's symbol on the headline line.
  - [x] 12c. Snapshot tests for each formatter cover both crypto (`BTC`) and non-crypto (`AUDUSD`) symbols to confirm there's no BTC-only string left. _(Acceptance: `pytest tests/unit/test_formatting.py` green.)_

- [x] **13. Rewrite `/streak` to report per-symbol.** _(R5.6)_
  - [x] 13a. In the `/streak` handler, walk all closed trades; group by symbol; for each symbol with closed history, compute the streak and active cap; render one line per symbol in symbol-sorted order. Empty state when no symbol has closed history: `No closed trades yet on any symbol.`
  - [x] 13b. Integration test: seed closed trades on three symbols (`BTC`, `ETH`, `HYPE`) with different histories; assert one line per symbol in stable order; assert empty-state copy on a fresh repo. _(Acceptance: `pytest tests/integration/test_streak_command.py` green.)_

## Phase 6 — Monitor and wiring

- [x] **14. Monitor: per-symbol mid lookup in the tick processing loop.** _(R3.2, R3.3, R3.4, R4.4)_
  - [x] 14a. In `src/monitor/monitor.py`, change the per-tick loop to: for each open trade `t`, read `mid = tick.symbol_for(t.symbol)` (or, if the adapter emits one tick per symbol, route ticks by `tick.symbol` to the right trade(s)). Skip trades whose symbol is missing from the current frame — never raise a false breach. _(R3.3)_
  - [x] 14b. On the `RECONNECTED` event with gap > 60s, trigger the existing gap-recovery callback to re-evaluate every open trade against the next frame. _(R4.4)_
  - [x] 14c. Update the `tick` event published on the event bus to include `symbol` in its payload (forward-compatible: the intelligence layer is empty in v1, so this only future-proofs the bus).
  - [x] 14d. Unit + e2e test: open trades on `BTC` and `ETH`; feed a frame where `ETH` breaches its invalidation but `BTC` is fine → exactly one breach record (for `ETH`), one alert, both keyed to the right trade. A second frame missing `BTC` while `ETH` is fine produces no breach and no false alert. _(Acceptance: `pytest tests/e2e/test_multi_symbol_breach.py` green; `src/monitor/` coverage stays ≥85%.)_

- [x] **15. Wire the Hyperliquid adapter and the universe refresh scheduler in `app.py`.** _(R3.1, R2.3)_
  - [x] 15a. Replace the Binance adapter construction with `HyperliquidExchangeAdapter(settings)`. Pass it to the monitor task as before.
  - [x] 15b. Register a background job (APScheduler or a small asyncio task) that calls `adapter.fetch_universe()` every `HYPERLIQUID_UNIVERSE_REFRESH_SECONDS` and writes the result via `repo.set_universe(...)`. On failure, log `universe_refresh_failed` and leave the existing cache in place; the next run retries on schedule.
  - [x] 15c. Make sure the monitor task starts only once a trade is open (single shared subscription kept up until all trades close, per R3.7) — or, equivalently, run the subscription always once boot is complete. The simpler "always up after boot" implementation is acceptable.

- [x] **16. Extend `/health` to expose Hyperliquid feed and universe status.** _(R4.4)_
  - [x] 16a. Add fields to the `/health` reply: `websocket: connected | disconnected | stale | unknown`, `last_frame_age_s`, `universe_cache_age_s`, `last_hyperliquid_error` (optional, truncated). Existing Redis health fields stay.
  - [x] 16b. Integration test exercises the formatter under all four websocket states. _(Acceptance: `pytest tests/integration/test_health_command.py` green.)_

## Phase 7 — Cleanup, migration, docs

- [x] **17. Update existing tests for the new `Tick` and `Trade` shapes.** _(R3.1, R1.1)_
  - [x] 17a. Sweep `tests/` for `Tick(...)` constructors and add the `symbol` positional arg (use `"BTC"` as the default in tests that don't care, to keep diffs small).
  - [x] 17b. Sweep for `Trade(...)` constructions and add `symbol="BTC"` to test fixtures that previously assumed the global symbol.
  - [x] 17c. Delete or rewrite any test still importing the deleted Binance/Bybit adapters. _(Acceptance: full `pytest` suite green after this task.)_

- [x] **18. Update `README.md` and `RUNBOOK.md`.** _(R6.4, R8.1)_
  - [x] 18a. README: remove BTC-only language. Document the symbol field on `/new`, the new env vars, and Hyperliquid as the sole price source. Add a small "supported markets" note pointing at `https://api.hyperliquid.xyz/info` for the live universe.
  - [x] 18b. RUNBOOK: add the universe-cache-stale operational case (symptom, log line, remediation = wait for next refresh or restart). Add the Hyperliquid feed status interpretation under `/health`. Document the clean-cutover procedure (stop → FLUSHDB → env-var diff → up). Note the rollback procedure (revert image tag; no v1 data to recover).
  - [x] 18c. Network-egress allowlist documentation updated: drop Binance host, add `api.hyperliquid.xyz`.

- [ ] **19. Cutover verification on a fresh Redis.** _(R8.1, R8.2)_
  - [ ] 19a. With a freshly-flushed Redis, start the stack, confirm `/health` shows `websocket: connected` within a minute and `universe_cache_age_s` becomes small after the first refresh.
  - [ ] 19b. `/new` on `BTC` → cancel. `/new` on a non-crypto Hyperliquid perp (e.g. `AUDUSD` if listed; otherwise `ETH`) → commit a tiny size, then `/setpnl 0` after closing, then `/streak` to confirm per-symbol display.
  - [ ] 19c. Sanity check: a trade opened on a symbol whose mid is currently in `allMids` triggers a breach correctly when its invalidation price is crossed (use a deliberately tight invalidation to force the case). _(Acceptance: manual checklist signed off by the operator before declaring the cutover complete.)_

## Coverage summary (requirement → task)

A quick scan to confirm every requirement is touched by at least one task.

| Requirement | Tasks |
|---|---|
| R1 — Per-trade symbol selection | 2, 11, 12, 17 |
| R2 — Symbol validation | 4g, 5f, 8, 9, 11, 15 |
| R3 — Multi-symbol monitoring via `allMids` | 1, 4, 5, 14, 15 |
| R4 — Connection handling & gap recovery | 4c, 4d, 5d, 5e, 14b, 16 |
| R5 — Uniform discipline rules, per-symbol streak | 3 (default threshold), 7, 10, 13 |
| R6 — Configuration changes | 3, 6, 18 |
| R7 — Display & command surface | 12, 13, 16 |
| R8 — Clean cutover | 6, 17, 18, 19 |

If a future change adds a requirement, this table is where you confirm it isn't dropped.
