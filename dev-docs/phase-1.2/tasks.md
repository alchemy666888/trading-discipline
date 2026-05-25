# Tasks: Edit Closed Trade Command

> Work through these in order. Check off each task only when its outcome is
> verified (its test passes / the behavior is observable). If a task can't be
> completed as written, stop and flag it rather than improvising outside the
> spec. Requirement IDs in _(parentheses)_ trace back to `requirements.md`.

Implementation language: **Python 3.11+**, consistent with the existing codebase.

## Phase 1: Foundations (no dependencies)

- [x] 1. Add the confirm step to the conversation-state enum.
  - Add `EDIT_CLOSED_CONFIRM` to `ConversationStep` in `src/models/conversation.py`.
  - Confirm `partial_trade_json` already accepts an arbitrary JSON blob (it does) so it can hold `{"trade_id", "updates", "recomputed_pnl"}`.
  - _(R5.1, R5.4)_

- [x] 2. Add `/edit_closed` formatting and help copy in `src/bot/formatting.py`.
  - [x] 2a. Define an `EDIT_CLOSED_FIELDS` tuple = direction, size_usdt, leverage, leverage_override_reason, entry_price, invalidation_price, max_loss_usdt, regime, thesis, opened_at, closed_at, close_price. _(R2.1)_
  - [x] 2b. `edit_closed_usage()`, `edit_closed_not_found(id)`, `edit_closed_not_closed(id)` (mentions `/edit`), `edit_closed_invalid_field(field)`, `edit_closed_validation_error(field, msg)`. _(R1.3, R1.4, R1.5, R1.6)_
  - [x] 2c. `edit_closed_preview(changes, recomputed_pnl, impact, pnl_override_warning)` rendering old→new per field, recomputed P&L when present, streak/cap before→after, and a `/setpnl` warning line when an override is overwritten, ending with the yes/no prompt. _(R4.3, R5.2, R5.3)_
  - [x] 2d. `edit_closed_applied(trade, changed_fields)` and `edit_closed_cancelled()`. _(R5.5, R5.6)_
  - [x] 2e. Add `/edit_closed` to `help_overview()` and a `help_map["edit_closed"]` entry. _(R6.1, R6.2)_

## Phase 2: Pure logic and persistence

- [x] 3. Implement the discipline-impact helper in `src/rules/impact.py`.
  - [x] 3a. Define a frozen `DisciplineImpact` dataclass: `streak_before, streak_after, cap_before, cap_after`. _(R5.3)_
  - [x] 3b. `discipline_impact(closed_trades, edited)`: build the post-edit list by swapping `edited` in by id, then compute streak and `compute_size_cap` over both the original and edited lists, reusing the existing pure functions in `src/rules/sizing.py`. _(R5.3)_
  - [x] 3c. Unit tests: a loss→win flip changes the streak and cap; editing a winner's size moves the cap; a non-P&L edit leaves both unchanged. _(R5.3)_

- [x] 4. Add `update_closed_trade` to `RedisRepository` (`src/db/repo.py`).
  - [x] 4a. `async def update_closed_trade(trade_id, *, updates, recomputed_pnl)`: WATCH the trade key, re-read, return `None` if status is no longer `CLOSED`. _(R5.5)_
  - [x] 4b. Merge editable fields, set `realized_pnl` to `recomputed_pnl` when provided, and validate the merged record through the `Trade` model before writing. _(R3.4, R4.1, R4.3)_
  - [x] 4c. In the MULTI/EXEC, rewrite `trades:all` score when `opened_at` changed and `trades:closed` score when `closed_at` changed (reuse the keyspace helpers). _(index integrity)_
  - [x] 4d. Integration tests (real Redis): only named fields change; non-editable fields preserved; non-`CLOSED` precondition aborts; `list_closed_trades` ordering reflects a `closed_at` edit; `list_all_trades` ordering reflects an `opened_at` edit. _(R2.9, R3.4)_

## Phase 3: Edit service

- [x] 5. Implement `ClosedTradeEditService` in `src/bot/edit_closed.py`. _(R1.4–R1.6, R2, R3, R4, R5)_
  - [x] 5a. `_preview_trade(current, updates)`: coerce raw string values to typed fields, merge onto the current trade, and construct a `Trade` (the pydantic model is the validation gate for invalidation side, leverage-override rule, and closed-field presence). _(R2.3–R2.9, R3.1–R3.4)_
  - [x] 5b. `prepare(chat_id, trade_id, raw_updates)`: reject missing trade (R1.4), non-`CLOSED` trade (R1.5), and non-editable field names (R1.6); reject `closed_at < opened_at` (R3.5); on per-field validator failure return a field-specific error and persist nothing (R2.3–R2.8).
  - [x] 5c. In `prepare`, recompute `realized_pnl` iff `direction/size_usdt/entry_price/close_price` is edited, and set the override-warning flag when the recomputed value replaces a differing stored value. _(R4.1, R4.2, R4.3)_
  - [x] 5d. In `prepare`, refuse when a `/new` form is already active for the chat (slot exclusivity), compute `discipline_impact`, write `EDIT_CLOSED_CONFIRM` state with the pending payload and TTL, and return the preview message. _(R5.1, R5.2, R5.3, R5.4)_
  - [x] 5e. `resolve(chat_id, text)`: return `None` if no edit-confirm is pending; on affirmative reply call `update_closed_trade`, clear state, return applied message (handle a WATCH abort gracefully); on any other reply clear state and return cancelled. _(R5.4, R5.5, R5.6)_
  - [x] 5f. Property/unit tests: non-editable fields never appear in the merged update; unspecified editable fields are preserved; P&L recompute fires exactly on the four P&L fields; each invariant rejection (wrong invalidation side per direction, high-leverage-without-reason, `closed_at < opened_at`) is enforced. _(R2.2, R2.9, R3, R4.1, R4.2)_

## Phase 4: Command surface

- [x] 6. Wire the command into `TelegramHandlers` (`src/bot/handlers.py`).
  - [x] 6a. Inject a `ClosedTradeEditService` instance into the handlers' constructor. _(R5)_
  - [x] 6b. `@whitelisted @safe_handler async def edit_closed(...)`: parse first arg as id and remaining `field=value` args (split on first `=`), reply usage on bad/empty input, else delegate to `prepare` and reply with its message. _(R1.1, R1.2, R1.3, R5.2)_
  - [x] 6c. In `text_message`, when the loaded conversation step is `EDIT_CLOSED_CONFIRM`, route the text to `resolve` before `TradeFormService.handle_input`. _(R5.4, R5.5, R5.6)_

- [x] 7. Make `/cancel` also clear a pending `EDIT_CLOSED_CONFIRM`.
  - Ensure the cancel path clears the conversation slot regardless of which step occupies it, replying with cancellation. _(R5.6)_

- [x] 8. Register the command.
  - Add `CommandHandler("edit_closed", handlers.edit_closed)` in the application builder. Confirm the registered token uses an underscore (Telegram disallows `-`). _(R1.1)_

## Phase 5: Verification

- [x] 9. End-to-end tests (handler + fake repo).
  - [x] 9a. Happy path: `/edit_closed` → preview shows old→new, recomputed P&L, and streak/cap before→after → `yes` → change persisted. _(R5.1–R5.5)_
  - [x] 9b. Decline path (`no`) leaves the trade unchanged; not-closed input redirects to `/edit`. _(R1.5, R5.6)_
  - [x] 9c. `/help` overview lists `/edit_closed`; `/help edit_closed` returns format and editable fields. _(R6.1, R6.2)_

- [x] 10. Checkpoint.
  - Run the full suite, linter, and type checker; confirm v1/`/edit`/`/setpnl` behavior is unchanged. Flag any spec gap rather than improvising.

## Notes

- Property tests assert universal correctness (field preservation, invariant enforcement); example tests cover specific edge cases.
- All new code follows the existing async patterns and centralizes user-facing copy in `formatting.py`.
- No new third-party dependencies.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3", "4"] },
    { "id": 2, "tasks": ["5"] },
    { "id": 3, "tasks": ["6", "7"] },
    { "id": 4, "tasks": ["8"] },
    { "id": 5, "tasks": ["9"] },
    { "id": 6, "tasks": ["10"] }
  ]
}
```
