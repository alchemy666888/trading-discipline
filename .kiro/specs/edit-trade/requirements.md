# Requirements Document

## Introduction

This document defines requirements for a new Telegram command to modify historical trades. The feature allows users to edit or adjust previously recorded trades through a new `/edit` bot command. This addresses the user need to correct mistakes in trade entries without requiring manual database intervention.

## Glossary

- **System**: The Telegram bot (BTC Discipline Bot)
- **Trade**: A committed trade record with fields including direction, size, leverage, entry price, invalidation price, max loss, regime, and thesis
- **Trade_ID**: Unique identifier for a trade record
- **Historical Trade**: A trade that has been committed and exists in the datastore
- **Edit Operation**: A modification to one or more fields of an existing trade record

## Requirements

### Requirement 1: Edit Trade Command

**User Story:** As a trader, I want to edit previously recorded trades through a Telegram command, so that I can correct errors without manual database intervention.

#### Acceptance Criteria

1. THE System SHALL accept an `/edit` command with trade ID and field updates
2. THE System SHALL validate that the trade exists before attempting modification
3. WHEN a non-existent trade ID is provided, THE System SHALL return an error message
4. THE System SHALL validate that the trade is in OPEN or OPEN_OVERRIDE status before allowing edits
5. WHEN a closed trade is referenced, THE System SHALL return an error indicating trades must be open to edit
6. THE System SHALL validate all edited fields using the same validation rules as the `/new` command
7. THE System SHALL preserve fields that are not explicitly provided in the edit command
8. THE System SHALL log the edit operation with timestamp and changed fields
9. THE System SHALL return a confirmation message with the updated trade details

### Requirement 2: Editable Trade Fields

**User Story:** As a trader, I want to edit specific fields of a trade, so that I can correct individual mistakes without re-entering the entire trade.

#### Acceptance Criteria

1. WHERE direction is provided, THE System SHALL validate it is either "long" or "short"
2. WHERE size_usdt is provided, THE System SHALL validate it is greater than zero
3. WHERE leverage is provided, THE System SHALL validate it is between 1 and 125 inclusive
4. WHERE entry_price is provided, THE System SHALL validate it is greater than zero
5. WHERE invalidation_price is provided, THE System SHALL validate it is on the correct side of entry based on direction
6. WHERE max_loss_usdt is provided, THE System SHALL validate it is greater than zero
7. WHERE regime is provided, THE System SHALL validate it is one of: uptrend, range, downtrend, event_risk
8. WHERE thesis is provided, THE System SHALL validate it is between 10 and 280 characters
9. THE System SHALL NOT allow editing of the following fields: id, opened_at, closed_at, close_price, realized_pnl, status, size_reduction_enforced

### Requirement 3: Leverage Override Handling During Edit

**User Story:** As a trader, I want the leverage override requirement to apply when editing leverage, so that high-leverage trades still require justification.

#### Acceptance Criteria

1. WHEN leverage is edited to a value >= 20, THE System SHALL require a leverage_override_reason
2. WHERE leverage_override_reason is provided, THE System SHALL validate it is between 10 and 500 characters
3. THE System SHALL preserve the existing leverage_override_reason when leverage is not changed
4. THE System SHALL clear the leverage_override_reason when leverage is edited to below threshold

### Requirement 4: Invalidation Price Validation

**User Story:** As a trader, I want invalidation price validation during edit, so that my trade protection remains logically consistent.

#### Acceptance Criteria

1. WHEN editing invalidation_price on a long trade, THE System SHALL validate invalidation_price < entry_price
2. WHEN editing invalidation_price on a short trade, THE System SHALL validate invalidation_price > entry_price
3. THE System SHALL use the current entry_price for validation when both are being edited in the same command

### Requirement 5: Edit Command Format

**User Story:** As a trader, I want a clear command format for editing trades, so that I know how to specify my edits.

#### Acceptance Criteria

1. THE System SHALL accept the format `/edit <trade_id> <field1>=<value1> [<field2>=<value2> ...]`
2. THE System SHALL support editing multiple fields in a single command
3. THE System SHALL return a usage message when the command format is incorrect
4. THE System SHALL display available editable fields in the usage message

### Requirement 6: Open Trade List Integration

**User Story:** As a trader, I want to see which trades are editable, so that I can reference trade IDs for editing.

#### Acceptance Criteria

1. THE `/open` command SHALL display trade IDs for all OPEN and OPEN_OVERRIDE trades
2. THE trade ID SHALL be clearly visible in the `/open` output
3. THE `/open` output SHALL indicate which trades have an active breach