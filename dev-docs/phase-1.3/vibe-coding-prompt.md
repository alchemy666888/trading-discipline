# Build Prompt: Multi-Asset Support (Hyperliquid)

You are an autonomous coding agent (Codex) extending an existing,
single-user, self-hosted Telegram trading-discipline bot. A complete,
approved spec already exists in this repository under
`specs/multi-asset-support/`. **Follow it exactly; do not redesign it.**

The change converts the bot from a single hard-coded instrument
(`BTCUSDT` via Binance) into a monitor for **any Hyperliquid perpetual**
(crypto, equities, oil, gold, forex such as `AUDUSD`), sourced from one
shared Hyperliquid `allMids` websocket subscription, with per-trade symbol
selection and per-symbol discipline streaks. The bot stays **monitoring
only** — it never places, modifies, or cancels orders, and the user
continues to self-report fills.

## Read first — these three files are the source of truth

Read all three, in full, before writing any code:

1. `specs/multi-asset-support/requirements.md` — **what** to build and the
   acceptance criteria, in EARS format (R1–R8). This is the contract.
2. `specs/multi-asset-support/design.md` — **how** to build it: architecture,
   the `Tick`/`Trade` contract changes, the Hyperliquid adapter, the universe
   cache and its graceful-degradation policy, the monitor loop, per-symbol
   sizing, config diff, error handling, and the testing strategy (§1–§12).
3. `specs/multi-asset-support/tasks.md` — the **ordered checklist** you will
   execute: 19 tasks across 7 phases, each citing the requirements it
   satisfies, each with an acceptance check.

When the spec answers a question, the spec wins over your own judgment.
Where the three files ever disagree, the precedence is
**requirements.md → design.md → tasks.md** (what beats how beats plan), and
you stop and flag the conflict rather than guessing.

## How to work — spec-driven, one task at a time

1. **Work through `tasks.md` top to bottom, one task at a time.** Do not jump
   ahead, batch unrelated tasks, or start a later phase before the current
   one's tasks are done and verified. The ordering is deliberate: Phase 1
   lands the rippling contract changes (`Tick.symbol`, `Trade.symbol`,
   `ConversationStep.SYMBOL`) so everything downstream compiles.
2. **Verify before checking off.** A task is done only when its stated
   acceptance check passes — usually the named `pytest` target. After
   verifying, check the task's box (and its sub-task boxes) in `tasks.md` by
   editing the file in place (`- [ ]` → `- [x]`). The checked file is the
   running record of progress.
3. **The spec is authoritative — stop and ask, don't improvise.** If a task
   is ambiguous, conflicts with the design, depends on something that isn't
   there, or simply looks wrong, **STOP and surface the question** instead of
   inventing a solution, papering over a gap, or adding unspecified features.
   An eager guess that drifts from the spec is worse than a paused build.
4. **Keep each change scoped to the current task.** Don't refactor unrelated
   code, rename things for taste, or "clean up" outside the task's stated
   files. Touch the broader codebase only where a task explicitly tells you
   to (e.g. Task 6 deleting the Binance/Bybit adapters, Task 17 sweeping
   existing tests for the new `Tick`/`Trade` shapes).
5. **Preserve invariants that the spec calls out as untouchable:**
   - The `src/intelligence/` layer is **read-only** (REQ-010): it may write
     only `signals:*` keys. No task here widens that boundary — keep it intact.
   - All user-facing copy stays centralized in `src/bot/formatting.py`.
   - The Redis key contract is unchanged except the documented additions
     (`trade:{id}` gains `symbol`; a new `hyperliquid:universe:perps` key that
     lives outside the trade/breach/alert/conversation/signals namespaces).
   - The `src/rules/` and `src/monitor/` packages must keep **≥85% line
     coverage** — the existing gate. Add tests as you go, not at the end.
6. **This is a clean break, not a migration.** Per R8 / design §10, existing
   `BTCUSDT` v1 data is wiped on cutover; do not write any data-migration
   code. Existing v1 tests that assume the global symbol are updated to the
   new shape (Task 17), not preserved as-is.

## Environment and conventions

Pull specifics from `design.md` §1, §6.7, and §9; do not re-derive them.

- **Stack:** Python 3.11+, asyncio. `python-telegram-bot` v20+, `redis.asyncio`,
  Pydantic v2 (+ pydantic-settings), `structlog`, APScheduler, `websockets`.
  This change **adds `httpx`** (for the Hyperliquid `info`/`meta` REST call) and
  **removes** the Binance and Bybit adapters and any Binance/Bybit deps.
- **External endpoints (public, no API key, TLS only):**
  `wss://api.hyperliquid.xyz/ws` (subscribe `{"type":"allMids"}`) and
  `https://api.hyperliquid.xyz/info` (POST `{"type":"meta"}` for the perp
  universe). No authenticated or private Hyperliquid endpoints. No order
  placement. Update the network-egress allowlist to permit `api.hyperliquid.xyz`
  and drop the Binance host (Task 6c / Task 18c).
- **Config (design §6.7):** remove `SYMBOL` and `EXCHANGE`; default
  `LEVERAGE_BLOCK_THRESHOLD` to `10`; add `HYPERLIQUID_WS_URL`,
  `HYPERLIQUID_INFO_URL`, `HYPERLIQUID_UNIVERSE_REFRESH_SECONDS` (300),
  `HYPERLIQUID_UNIVERSE_STALE_SECONDS` (900), `HYPERLIQUID_FEED_STALE_SECONDS`
  (30), `HYPERLIQUID_FEED_REQUEST_TIMEOUT_SECONDS` (5). Keep `.env.example`
  in sync.
- **Run / test commands:** use the repository's existing tooling — check
  `pyproject.toml`, `Makefile`, `docker-compose.yml`, and any `conftest.py`
  **before** assuming an invocation, and prefer whatever the repo already
  defines. If the repo provides no wrapper, the conventional commands for this
  stack are:
  - Install dev deps: `pip install -e ".[dev]"` (or `poetry install` /
    `make install` if the repo uses them).
  - Full suite: `pytest`
  - A single task's target, e.g.: `pytest tests/adapter/test_hyperliquid.py`
  - Coverage gate check: `pytest --cov=src/rules --cov=src/monitor
    --cov-report=term-missing` and confirm both stay ≥85%.
  - Run the bot locally: `docker compose up -d` (Redis must be reachable with
    AOF enabled, per the existing startup checks).
  Tests use `pytest` + `pytest-asyncio` + `freezegun` (for backoff/staleness
  timing) + `hypothesis` (existing property tests). Mock the websocket and
  `httpx` at the adapter boundary — never hit the live Hyperliquid endpoints
  in tests.
- **Key facts the design flags as easy to get wrong (design §12):**
  - `allMids` carries **no per-symbol timestamp** — use the frame receive time
    as the `Tick.ts`.
  - **Not every open trade's symbol appears in every frame** — when a symbol is
    missing, skip that trade for that frame; never raise a false breach.
  - Mid prices arrive as **strings** — parse to `float`, and skip+log any
    malformed value rather than emitting a bad tick.
  - The per-symbol streak filter must apply **everywhere** sizing is computed —
    the form's size step, `/streak`, and the `/edit_closed` impact preview.
  - `allMids` is O(all listed coins), not O(open trades) — iterate open trades
    and dict-look-up each symbol; do not invert that loop.

## Definition of done

- Every task in `tasks.md` is checked off (`- [x]`), in order, each verified
  by its acceptance check.
- The full `pytest` suite passes, including the updated existing tests
  (Task 17) and the new adapter/integration/e2e tests.
- `src/rules/` and `src/monitor/` remain at ≥85% line coverage; the new
  `src/exchange/hyperliquid.py` is ≥85% covered by its adapter tests.
- Every acceptance criterion in `requirements.md` (R1–R8) is satisfiable, and
  the coverage table at the bottom of `tasks.md` still maps each requirement
  to at least one completed task.
- The bot boots via `docker compose up -d` against a fresh (flushed) Redis,
  `/health` reports `websocket: connected` and a fresh universe cache, and the
  Task 19 manual cutover checklist passes (multi-symbol `/new`, a forced
  breach, and per-symbol `/streak`).
- No Binance/Bybit references remain anywhere in `src/`, tests, or docs; the
  `src/intelligence/` read-only boundary is intact.

## Start here

1. Read `requirements.md`, then `design.md`, then `tasks.md` in full.
2. Briefly confirm your understanding back to me: the goal, the three rippling
   Phase 1 contract changes, and the per-symbol-streak rule — so I know you've
   absorbed the spec, not just skimmed it.
3. Begin **Task 1** (the `Tick` contract change). Work the checklist in order,
   checking off boxes as you verify each task, and **stop and ask** the moment
   anything is ambiguous or conflicts with the spec.
