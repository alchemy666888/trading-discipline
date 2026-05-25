# Implementation Plan: Edit Trade Command

## Overview

This implementation plan creates a new `/edit` command for the BTC Discipline Bot that allows traders to modify previously recorded trades. The feature reuses existing infrastructure (TradeFormService for validation, RedisRepository for persistence, formatting module for messages) and follows the established command pattern in the codebase.

Implementation language: **Python**

## Tasks

- [x] 1. Add formatting functions for /edit command responses
  - Add `edit_usage()` - Returns command usage message with available editable fields
  - Add `edit_trade_not_found(trade_id)` - Returns error when trade doesn't exist
  - Add `edit_trade_closed(trade_id)` - Returns error when trade is closed
  - Add `edit_confirmation(trade, updated_fields)` - Returns success message with updated details
  - Add `edit_validation_error(field, message)` - Returns field validation error
  - Add `edit_invalid_format()` - Returns format error for invalid field=value pairs
  - Add `edit_invalid_field(field)` - Returns error for unknown field
  - Update `help_overview()` and `help_map` to include `/edit` command
  - Enhance `format_open_trades()` to display trade IDs clearly (Requirement 6.1, 6.2)
  - _Requirements: 1.9, 5.3, 5.4, 6.1, 6.2, 6.3_

- [x] 2. Add update_trade method to repository
  - [x] 2.1 Add `update_trade(trade_id: int, updates: dict)` method to RedisRepository
    - Load existing trade and validate it's open
    - Apply only editable fields from updates dict
    - Handle leverage_override_reason persistence/clearing
    - Use Redis WATCH/MULTI/EXEC for atomic updates
    - Return updated trade object
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

  - [x] 2.2 Add serialization helper for selective field updates
    - Filter updates to only editable fields (reject: id, opened_at, closed_at, close_price, realized_pnl, status, size_reduction_enforced)
    - _Requirements: 2.9_

  - [x] 2.3 Write unit tests for update_trade
    - Test non-existent trade returns error
    - Test closed trade returns error
    - Test only editable fields are modified
    - Test non-editable fields preserved
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 2.9_

- [x] 3. Add cross-field validation helpers
  - [x] 3.1 Add `validate_invalidation_for_edit(direction, entry_price, invalidation_price)`
    - Validate invalidation_price is on correct side of entry_price
    - For long: invalidation_price < entry_price
    - For short: invalidation_price > entry_price
    - _Requirements: 2.5, 4.1, 4.2, 4.3_

  - [x] 3.2 Add `calculate_leverage_override(current_leverage, new_leverage, current_reason, new_reason)`
    - Return tuple: (should_require_reason, should_clear_reason, final_reason)
    - Handle leverage >= 20 requiring justification
    - Clear reason when leverage reduced below threshold
    - Preserve existing reason when leverage unchanged
    - _Requirements: 3.1, 3.3, 3.4_

  - [x] 3.3 Write unit tests for cross-field validation
    - Test invalidation side validation for long trades
    - Test invalidation side validation for short trades
    - Test high leverage requires reason
    - Test leverage reduction clears reason
    - Test leverage preservation keeps reason
    - _Requirements: 3.1, 3.3, 3.4, 4.1, 4.2_

- [ ] 4. Add /edit command handler
  - [x] 4.1 Add `edit()` method to TelegramHandlers
    - Use `@whitelisted` and `@safe_handler` decorators
    - Parse command format: `/edit <trade_id> field1=value1 [field2=value2 ...]`
    - Extract trade_id from first argument
    - Parse field=value pairs from remaining arguments
    - Return usage message if format is invalid
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ] 4.2 Implement trade existence and status validation
    - Fetch trade by ID
    - Return error if trade not found
    - Return error if trade status is CLOSED (only OPEN/OPEN_OVERRIDE allowed)
    - _Requirements: 1.2, 1.3, 1.4, 1.5_

  - [~] 4.3 Implement field validation
    - Validate each provided field using existing TradeDraft validators
    - Direction: must be "long" or "short" (Requirement 2.1)
    - size_usdt: must be > 0 (Requirement 2.2)
    - leverage: must be 1-125 inclusive (Requirement 2.3)
    - entry_price: must be > 0 (Requirement 2.4)
    - invalidation_price: validate against direction (Requirement 2.5, 4.1, 4.2)
    - max_loss_usdt: must be > 0 (Requirement 2.6)
    - regime: must be one of uptrend, range, downtrend, event_risk (Requirement 2.7)
    - thesis: must be 10-280 characters (Requirement 2.8)
    - leverage_override_reason: 10-500 chars when leverage >= 20 (Requirement 3.2)
    - _Requirements: 1.6, 2.1-2.8, 3.2_

  - [~] 4.4 Apply cross-field validation
    - Run invalidation_price validation against entry_price
    - Handle leverage override logic
    - _Requirements: 3.1, 3.3, 3.4, 4.1, 4.2, 4.3_

  - [~] 4.5 Persist changes and return confirmation
    - Call repository.update_trade()
    - Log edit operation with timestamp and changed fields
    - Return formatted confirmation with updated trade details
    - _Requirements: 1.8, 1.9_

  - [~] 4.6 Write property test for edit command handler
    - **Property 1: Field preservation**
    - **Validates: Requirements 1.7, 2.9**

  - [~] 4.7 Write property test for non-editable fields
    - **Property 2: Non-editable fields immutable**
    - **Validates: Requirements 2.9**

  - [~] 4.8 Write property test for invalidation side validation
    - **Property 3: Long trade invalidation side**
    - **Property 4: Short trade invalidation side**
    - **Validates: Requirements 2.5, 4.1, 4.2, 4.3**

  - [~] 4.9 Write property test for leverage override
    - **Property 5: High leverage justification requirement**
    - **Property 6: Leverage reduction clears override reason**
    - **Property 7: Leverage reason preservation**
    - **Validates: Requirements 3.1, 3.3, 3.4**

  - [~] 4.10 Write property test for field validation consistency
    - **Property 8: Field validation rules consistency**
    - **Validates: Requirements 1.6, 2.1-2.8, 3.2_

- [x] 5. Register /edit command with Telegram bot
  - Add `/edit` to the command list in Application builder
  - _Requirements: 5.1_

- [ ] 6. Integration test
  - [~] 6.1 Write integration test for complete edit flow
    - Create trade via /new, edit via /edit, verify changes persisted
    - Test edit followed by close works correctly
    - _Requirements: 1.1, 1.9_

- [~] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- The design uses async Python patterns consistent with existing codebase
- Property tests validate universal correctness properties using hypothesis
- Unit tests validate specific examples and edge cases
- Cross-field validation ensures invalidation_price is on correct side of entry_price

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.2"] },
    { "id": 2, "tasks": ["2.3", "3.1", "3.2"] },
    { "id": 3, "tasks": ["3.3", "4.1", "4.2", "4.3", "4.4", "4.5"] },
    { "id": 4, "tasks": ["4.6", "4.7", "4.8", "4.9", "4.10", "5.1"] },
    { "id": 5, "tasks": ["6.1"] }
  ]
}
```
