# Requirements: Edit Closed Trade Command

## Introduction

The BTC Discipline Bot can edit a trade only while it is open (`/edit`) and can override the realized P&L of a closed trade (`/setpnl`). There is no way to correct a data-entry mistake in the rest of a *closed* trade's record — a wrong entry price, a mis-typed close price, an incorrect regime tag — without editing Redis by hand. This feature adds a dedicated `/edit_closed` command that lets the single trader correct any field of a historical (closed) trade.

Because closed trades feed the bot's discipline math — the consecutive-loss streak that caps the next trade's size, and the `/stats` adherence and P&L numbers — editing them is treated as a deliberate, reviewable correction rather than a casual change. The command therefore shows the trader how the edit moves the loss streak and active size cap, and requires an explicit confirmation before it writes.

- **Who it is for:** the single discretionary BTC trader who already uses the bot.
- **Problem solved:** correct errors in already-closed trade records without manual datastore surgery, while keeping the trader aware that history (and the discipline it drives) is changing.

## Goals

- **G1** Provide a `/edit_closed` command that edits the fields of a trade in `CLOSED` status.
- **G2** Allow correction of any data field of a closed trade, validated by the same rules as `/new` and `/edit`.
- **G3** Keep the stored record a valid closed trade after every edit (close fields present, invalidation on the correct side of entry).
- **G4** Recompute realized P&L automatically when an edited field changes the P&L outcome.
- **G5** Before writing, show the trader how the edit changes the consecutive-loss streak and the active size cap, and require explicit confirmation.
- **G6** Keep the discipline boundary intact: editing data must never silently weaken a rule without the trader seeing the effect.

## Non-Goals

- **NG1** No edit-history / audit trail. Edits overwrite values in place.
- **NG2** No reopening or status change. `/edit_closed` does not move a trade out of `CLOSED`; `id` and `status` are never editable here.
- **NG3** No change to the access model. Still single-user, whitelisted-chat-only, Telegram-only.
- **NG4** Not a replacement for `/setpnl`. `/setpnl` remains the shortcut for a manual P&L override (fees, partial fills); `/edit_closed` recomputes P&L from the standard formula.
- **NG5** No editing of `/edit` (open-trade) behavior; that command is unchanged.
- **NG6** No bulk edit across multiple trades in one command.

## Glossary

- **System** — the Telegram bot (BTC Discipline Bot).
- **Closed trade** — a trade record in `CLOSED` status; it has `closed_at`, `close_price`, and `realized_pnl` set.
- **Editable field** — a stored trade field the trader may correct via `/edit_closed` (see Requirement 2).
- **Discipline impact** — the change to the consecutive-loss streak and the active size cap that results from an edit.
- **Confirmation** — an explicit affirmative reply from the trader that authorizes the pending edit to be written.

## Requirements

### Requirement 1: The `/edit_closed` command
**User story:** As the trader, I want a command that targets a specific closed trade by id, so that I can correct its recorded data without touching the datastore directly.

**Acceptance criteria:**
1. The system SHALL register a Telegram command named `edit_closed`.
2. WHEN a non-whitelisted chat issues `/edit_closed` THEN the system SHALL ignore the command (consistent with all other commands).
3. WHEN `/edit_closed` is issued without a trade id and at least one `field=value` pair THEN the system SHALL reply with a usage message listing the accepted format and editable fields.
4. WHEN the supplied trade id does not exist THEN the system SHALL reply that the trade was not found and make no change.
5. IF the supplied trade id refers to a trade that is not in `CLOSED` status THEN the system SHALL reply that `/edit_closed` only edits closed trades and SHALL suggest `/edit` for open trades, and make no change.
6. WHEN one or more `field=value` pairs reference a field that is not editable THEN the system SHALL reply identifying the offending field and make no change.

### Requirement 2: Editable fields
**User story:** As the trader, I want to correct any data field of a closed trade, so that the historical record reflects what actually happened.

**Acceptance criteria:**
1. The system SHALL accept edits to: `direction`, `size_usdt`, `leverage`, `leverage_override_reason`, `entry_price`, `invalidation_price`, `max_loss_usdt`, `regime`, `thesis`, `opened_at`, `closed_at`, and `close_price`.
2. The system SHALL NOT accept edits to `id`, `status`, `realized_pnl`, or `size_reduction_enforced`.
3. WHERE `direction` is provided THEN the system SHALL require it to be `long` or `short`.
4. WHERE `size_usdt`, `entry_price`, `invalidation_price`, `max_loss_usdt`, or `close_price` is provided THEN the system SHALL require it to be greater than zero.
5. WHERE `leverage` is provided THEN the system SHALL require it to be an integer in the inclusive range 1–125.
6. WHERE `regime` is provided THEN the system SHALL require it to be one of `uptrend`, `range`, `downtrend`, `event_risk`.
7. WHERE `thesis` is provided THEN the system SHALL require it to be 10–280 characters.
8. WHERE `opened_at` or `closed_at` is provided THEN the system SHALL require a valid timezone-aware timestamp.
9. The system SHALL preserve every editable field that is not named in the command.

### Requirement 3: Closed-trade invariants preserved
**User story:** As the trader, I want the bot to reject edits that would corrupt a closed trade, so that I cannot accidentally create an invalid record.

**Acceptance criteria:**
1. WHEN an edit would leave `invalidation_price` on the wrong side of `entry_price` for the resulting `direction` (long: invalidation < entry; short: invalidation > entry) THEN the system SHALL reject the edit and make no change.
2. WHEN `leverage` is set to 20 or greater and no `leverage_override_reason` of 10–500 characters is present after the edit THEN the system SHALL reject the edit and make no change.
3. IF `leverage` is edited to below 20 THEN the system SHALL clear `leverage_override_reason`.
4. The system SHALL keep `closed_at`, `close_price`, and `realized_pnl` populated on the trade after any successful edit.
5. WHEN `closed_at` is edited to a value earlier than `opened_at` THEN the system SHALL reject the edit and make no change.

### Requirement 4: Realized P&L recomputation
**User story:** As the trader, I want P&L to follow automatically from corrected trade figures, so that the record stays internally consistent.

**Acceptance criteria:**
1. WHEN an edit changes `direction`, `size_usdt`, `entry_price`, or `close_price` THEN the system SHALL recompute `realized_pnl` from the standard formula used at close.
2. WHEN an edit does not change any P&L-determining field THEN the system SHALL leave `realized_pnl` unchanged.
3. WHERE a prior manual P&L override (via `/setpnl`) exists AND the edit recomputes `realized_pnl` THEN the system SHALL overwrite the override with the recomputed value and SHALL state this in the confirmation prompt.
4. The system SHALL direct the trader to `/setpnl` if they want a manual P&L value instead of a recomputed one.

### Requirement 5: Discipline-impact warning and confirmation
**User story:** As the trader, I want to see how an edit moves my discipline state and confirm before it lands, so that I never quietly rewrite the rules I set for myself.

**Acceptance criteria:**
1. WHEN a valid `/edit_closed` request is received THEN the system SHALL compute a preview of the resulting trade without yet persisting it.
2. WHEN the preview is ready THEN the system SHALL present the fields that will change (old → new), the recomputed `realized_pnl` if applicable, and the discipline impact, then request confirmation.
3. The discipline impact SHALL state the consecutive-loss streak and the active size cap both before and after the edit.
4. WHILE a pending `/edit_closed` confirmation is outstanding the system SHALL NOT modify the trade.
5. IF the trader confirms THEN the system SHALL apply the edit atomically and reply with the updated trade.
6. IF the trader declines or the confirmation is not given THEN the system SHALL discard the pending edit and make no change.

### Requirement 6: Help and discoverability
**User story:** As the trader, I want `/edit_closed` documented in help, so that I can recall its format.

**Acceptance criteria:**
1. The system SHALL list `/edit_closed` in the `/help` overview.
2. WHEN `/help edit_closed` is issued THEN the system SHALL return the command's format and editable fields.
