"""
v2 intelligence layer.

This module is intentionally empty in v1. v2 will add:
- News ingestion adapters (X, RSS, exchange announcements)
- Funding-rate and ETF-flow adapters
- LLM client(s) for classification and summarization
- A regime classifier
- Signal emitters that write to the `signals` Redis namespace
  and publish on the event bus

Read-only constraint (REQ-010):
- This module may write to `signals` and publish events.
- This module MUST NOT write to `trades`, `breaches`, `alerts`, or `conversation_state`.
- This module MUST NOT influence whether a trade is opened, blocked, sized, or closed.
- Discipline enforcement stays in src/rules/ and is deterministic.
"""
