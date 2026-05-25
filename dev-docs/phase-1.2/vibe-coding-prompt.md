# Build Prompt: Edit Closed Trade Command

You are an autonomous coding agent extending the **BTC Discipline Bot** with a new
`/edit_closed` Telegram command. A complete spec for this feature already exists
alongside this prompt. Follow it; don't redesign it.

## Read first (source of truth)
- `requirements.md` — what to build and the acceptance criteria (EARS format, R1–R6).
- `design.md` — the architecture, components, data model, and flows to implement.
- `tasks.md` — the ordered checklist you will execute (10 tasks across 5 phases, with a dependency-graph block at the end).

Read all three before writing any code. Also skim the existing codebase first —
this is an addition to a working system, and the design deliberately reuses its
patterns (`@whitelisted/@safe_handler` handlers, the conversation-state machine,
shared field validators in `src/rules/validation.py`, the WATCH/MULTI/EXEC write
pattern, and centralized copy in `src/bot/formatting.py`).

## How to work
1. Work through `tasks.md` top to bottom, one task at a time, respecting the
   dependency-graph waves.
2. After completing a task, verify its outcome (its test passes / behavior is
   observable), then check its box in `tasks.md`.
3. The spec is authoritative. If a task is ambiguous, conflicts with the design,
   or looks wrong, **STOP and ask — do not improvise outside the spec.**
4. Keep each change scoped to the task at hand. Do not refactor unrelated code.

## Feature-specific guardrails (do not violate)
- **Do not change `/edit` or `/setpnl` behavior.** `/edit` stays open-trades-only;
  `/setpnl` stays the manual P&L override. `/edit_closed` is additive.
- **Preserve the discipline boundary.** Editing a closed trade must never silently
  change the loss streak or size cap — the preview must show the before→after
  impact and nothing is written until the trader confirms (R5).
- **Confirmation reuses the conversation-state machine** (new step
  `EDIT_CLOSED_CONFIRM`); do not introduce inline-keyboard / callback handlers.
- **The registered command token is `edit_closed`** (underscore). Telegram
  command names allow only letters, digits, and underscores — `/edit-closed`
  is invalid.
- **Editing a timestamp must re-index the sorted sets** (`trades:all` by
  `opened_at`, `trades:closed` by `closed_at`), or `/stats` and
  `recent_closed_trades` ordering will silently break.
- **`realized_pnl` is derived here**, recomputed only when
  `direction/size_usdt/entry_price/close_price` changes; a recompute that
  overwrites a prior `/setpnl` value must be flagged in the preview (R4.3).
- Centralize all user-facing strings in `src/bot/formatting.py`. No new
  third-party dependencies.

## Environment
- Stack: Python 3.11+, `python-telegram-bot` v20+, `redis.asyncio`, `pydantic` v2, `structlog`.
- Install: `python -m pip install -e .`
- Run the bot: `python -m src.app`
- Lint / format / types: `ruff check .` && `black --check .` && `mypy --strict src/`
- Tests: `pytest` (asyncio auto-mode; the `network` marker is excluded by default).
- Integration tests spin up a real Redis container, so **Docker must be available**
  when running the `tests/integration` suite.
- Coverage gate: CI requires `src/rules/` to stay **≥ 85%** covered — this now
  includes the new `src/rules/impact.py`, so ship its unit tests with it.

## Definition of done
- Every task in `tasks.md` is checked off.
- Every acceptance criterion in `requirements.md` (R1.1–R6.2) is satisfied and
  referenced by a test.
- `/edit_closed` works end to end: prepare → preview (old→new, recomputed P&L,
  streak/cap before→after) → confirm → atomic write with correct re-indexing;
  decline and not-closed paths behave per spec.
- `/edit`, `/setpnl`, and all existing v1 behavior are unchanged.
- The project runs and the full suite passes under `ruff`, `black --check`,
  `mypy --strict`, and `pytest`, meeting the `src/rules/` coverage gate.

Start by reading the three spec files and skimming the existing handler, repo,
forms, and validation modules. Then confirm your understanding of the plan and
begin Task 1.
