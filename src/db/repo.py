"""Redis-backed repository and serializers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast

from redis import WatchError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.db import keyspace, migrations
from src.models.alert import Alert
from src.models.breach import Breach, BreachUserResponse
from src.models.conversation import ConversationState
from src.models.signal import Severity, Signal
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus
from src.rules import validation as validate

T = TypeVar("T")

_SCRIPT_DIR = Path(__file__).resolve().parent / "scripts"

# Fields that cannot be edited after trade creation
NON_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "id",
    "opened_at",
    "closed_at",
    "close_price",
    "realized_pnl",
    "status",
    "size_reduction_enforced",
})

# Fields that can be edited on open trades
EDITABLE_FIELDS: frozenset[str] = frozenset({
    "direction",
    "size_usdt",
    "leverage",
    "leverage_override_reason",
    "entry_price",
    "invalidation_price",
    "max_loss_usdt",
    "regime",
    "thesis",
})


def filter_editable_fields(updates: dict[str, Any]) -> dict[str, Any]:
    """Filter a dict of field updates to only include editable fields.

    Rejects any keys that are in NON_EDITABLE_FIELDS (id, opened_at, closed_at,
    close_price, realized_pnl, status, size_reduction_enforced).

    Args:
        updates: Raw dict of field updates from edit command.

    Returns:
        Dict containing only editable fields.

    Example:
        >>> filter_editable_fields({"size_usdt": 1000, "id": 1})
        {"size_usdt": 1000}
    """
    return {
        key: value
        for key, value in updates.items()
        if key not in NON_EDITABLE_FIELDS
    }


class RepositoryError(RuntimeError):
    """Base repository error."""


class RedisHealthError(RepositoryError):
    """Raised when Redis health or persistence requirements are unmet."""


@dataclass(frozen=True)
class RedisHealthDetails:
    """Redis connectivity and persistence status for `/health`."""

    connected: bool
    appendonly_enabled: bool | None
    persistence_dir: str | None
    persistence_dir_writable: bool | None
    aof_last_write_status: str | None
    last_error: str | None = None

    @classmethod
    def unknown(cls) -> RedisHealthDetails:
        return cls(
            connected=False,
            appendonly_enabled=None,
            persistence_dir=None,
            persistence_dir_writable=None,
            aof_last_write_status=None,
            last_error="unknown",
        )


class RedisRepository:
    """Repository boundary for all Redis access."""

    def __init__(self, redis_client: Redis) -> None:
        self._client = redis_client
        self._scripts = {
            "close_trade": (_SCRIPT_DIR / "close_trade.lua").read_text(
                encoding="utf-8"
            ),
            "mark_override": (_SCRIPT_DIR / "mark_override.lua").read_text(
                encoding="utf-8"
            ),
            "resolve_breach": (_SCRIPT_DIR / "resolve_breach.lua").read_text(
                encoding="utf-8"
            ),
        }

    async def apply_migrations(self) -> int:
        """Apply Redis schema migrations."""

        return await migrations.apply_migrations(self._client)

    async def verify_redis_ready(self) -> RedisHealthDetails:
        """Fail fast if Redis is unavailable or persistence is misconfigured."""

        health = await self.get_redis_health()
        if not health.connected:
            msg = f"Redis unavailable: {health.last_error or 'unknown error'}"
            raise RedisHealthError(msg)
        if health.appendonly_enabled is not True:
            msg = "Redis append-only persistence is disabled."
            raise RedisHealthError(msg)
        if health.persistence_dir_writable is False:
            msg = (
                "Redis append-only persistence directory is not writable: "
                f"{health.persistence_dir or 'unknown'}"
            )
            raise RedisHealthError(msg)
        return health

    async def get_redis_health(self) -> RedisHealthDetails:
        """Collect Redis connectivity and persistence health details."""

        try:
            await self._resolve_redis_call(self._client.ping())
            persistence_info = await self._resolve_redis_call(
                self._client.info(section="persistence")
            )
            appendonly_config = await self._resolve_redis_call(
                self._client.config_get("appendonly")
            )
            dir_config = await self._resolve_redis_call(self._client.config_get("dir"))
        except RedisError as exc:
            return RedisHealthDetails(
                connected=False,
                appendonly_enabled=None,
                persistence_dir=None,
                persistence_dir_writable=None,
                aof_last_write_status=None,
                last_error=str(exc),
            )

        appendonly_enabled = self._parse_appendonly_enabled(
            appendonly_config.get("appendonly"),
            persistence_info.get("aof_enabled"),
        )
        aof_last_write_status = self._as_text(
            persistence_info.get("aof_last_write_status")
        )
        persistence_dir_writable: bool | None = None
        if appendonly_enabled:
            persistence_dir_writable = aof_last_write_status == "ok"

        return RedisHealthDetails(
            connected=True,
            appendonly_enabled=appendonly_enabled,
            persistence_dir=self._as_text(dir_config.get("dir")),
            persistence_dir_writable=persistence_dir_writable,
            aof_last_write_status=aof_last_write_status,
            last_error=None,
        )

    async def create_trade(
        self,
        draft: TradeDraft,
        *,
        opened_at: datetime,
        status: TradeStatus = TradeStatus.OPEN,
        size_reduction_enforced: bool | None = None,
    ) -> Trade:
        """Create a committed trade and all indexes atomically."""

        trade_id = await self._client.incr(keyspace.trade_id_sequence_key())
        trade = self._trade_from_draft(
            trade_id=trade_id,
            draft=draft,
            opened_at=opened_at,
            status=status,
            size_reduction_enforced=size_reduction_enforced,
        )

        mapping = self._serialize_trade(trade)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(keyspace.trade_key(trade.id), mapping=mapping)
            pipe.zadd(
                keyspace.trades_all_key(),
                {str(trade.id): self._datetime_to_epoch(trade.opened_at)},
            )
            pipe.sadd(keyspace.trades_status_key(trade.status), str(trade.id))
            await pipe.execute()

        return trade

    async def get_trade(self, trade_id: int) -> Trade | None:
        """Load a single trade by ID."""

        data = await self._resolve_redis_call(
            self._client.hgetall(keyspace.trade_key(trade_id))
        )
        if not data:
            return None
        return self._deserialize_trade(data)

    async def list_all_trades(self) -> list[Trade]:
        """Return every trade ordered by opened_at ascending."""

        raw_ids = await self._resolve_redis_call(
            self._client.zrange(keyspace.trades_all_key(), 0, -1)
        )
        trades: list[Trade] = []
        for raw_id in raw_ids:
            trade = await self.get_trade(self._require_positive_int_from_redis(raw_id))
            if trade is not None:
                trades.append(trade)
        return trades

    async def list_open_trades(self) -> list[Trade]:
        """List trades in OPEN or OPEN_OVERRIDE status."""

        open_ids = await self._resolve_redis_call(
            self._client.smembers(keyspace.trades_status_key(TradeStatus.OPEN))
        )
        override_ids = await self._resolve_redis_call(
            self._client.smembers(keyspace.trades_status_key(TradeStatus.OPEN_OVERRIDE))
        )
        trade_ids = {
            self._require_positive_int_from_redis(raw_id)
            for raw_id in {*open_ids, *override_ids}
        }
        trades: list[Trade] = []
        for trade_id in trade_ids:
            trade = await self.get_trade(trade_id)
            if trade is not None:
                trades.append(trade)
        return sorted(trades, key=lambda trade: trade.opened_at)

    async def close_trade(
        self,
        trade_id: int,
        *,
        close_price: float,
        closed_at: datetime,
        breach_id: int | None = None,
        response_at: datetime | None = None,
        _allow_retry: bool = True,
    ) -> Trade | None:
        """Close a trade and optionally resolve the active breach in one script."""

        trade = await self.get_trade(trade_id)
        if trade is None or trade.status == TradeStatus.CLOSED:
            return None

        realized_pnl = self._calculate_realized_pnl(trade, close_price)
        breach_id_str = "" if breach_id is None else str(breach_id)
        resolved_at = response_at or closed_at

        result = await self._eval_script(
            "close_trade",
            keys=[
                keyspace.trade_key(trade_id),
                keyspace.trades_status_key(TradeStatus.OPEN),
                keyspace.trades_status_key(TradeStatus.OPEN_OVERRIDE),
                keyspace.trades_status_key(TradeStatus.CLOSED),
                keyspace.trades_closed_key(),
                keyspace.breach_active_key(trade_id),
                keyspace.breach_key(breach_id or 1),
                keyspace.breaches_unresolved_key(),
            ],
            args=[
                self._serialize_datetime(closed_at),
                self._serialize_float(close_price),
                self._serialize_float(realized_pnl),
                breach_id_str,
                self._serialize_datetime(resolved_at),
                str(trade_id),
                self._serialize_epoch(closed_at),
            ],
        )

        result_code = int(result)
        if result_code == -2 and breach_id is None and _allow_retry:
            concurrent_breach = await self.get_open_breach(trade_id)
            if concurrent_breach is None:
                return await self.close_trade(
                    trade_id,
                    close_price=close_price,
                    closed_at=closed_at,
                    breach_id=None,
                    response_at=response_at,
                    _allow_retry=False,
                )
            return await self.close_trade(
                trade_id,
                close_price=close_price,
                closed_at=closed_at,
                breach_id=concurrent_breach.id,
                response_at=response_at,
                _allow_retry=False,
            )

        if result_code != 1:
            return None
        return await self.get_trade(trade_id)

    async def mark_override(
        self,
        trade_id: int,
        *,
        breach_id: int,
        justification: str,
        response_at: datetime,
    ) -> Trade | None:
        """Resolve a breach with justification and set trade status to OPEN_OVERRIDE."""

        result = await self._eval_script(
            "mark_override",
            keys=[
                keyspace.trade_key(trade_id),
                keyspace.trades_status_key(TradeStatus.OPEN),
                keyspace.trades_status_key(TradeStatus.OPEN_OVERRIDE),
                keyspace.breach_key(breach_id),
                keyspace.breach_active_key(trade_id),
                keyspace.breaches_unresolved_key(),
            ],
            args=[
                str(breach_id),
                str(trade_id),
                self._serialize_datetime(response_at),
                justification,
            ],
        )

        if int(result) != 1:
            return None
        return await self.get_trade(trade_id)

    async def create_breach(
        self,
        trade_id: int,
        *,
        breach_price: float,
        detected_at: datetime,
    ) -> Breach | None:
        """Create a breach iff the trade is still open and unarmed."""

        breach_id = await self._client.incr(keyspace.breach_id_sequence_key())
        breach = Breach(
            id=breach_id,
            trade_id=trade_id,
            detected_at=self._ensure_aware_datetime(detected_at),
            breach_price=breach_price,
            user_response=None,
            response_at=None,
            justification=None,
        )
        mapping = self._serialize_breach(breach)

        trade_key_name = keyspace.trade_key(trade_id)
        active_breach_key = keyspace.breach_active_key(trade_id)
        async with self._client.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(trade_key_name, active_breach_key)
                    trade_data = await self._resolve_redis_call(
                        pipe.hgetall(trade_key_name)
                    )
                    active_breach = await self._resolve_redis_call(
                        pipe.get(active_breach_key)
                    )
                    if not trade_data:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return None

                    trade_status = self._as_text(
                        self._normalize_hash(trade_data).get("status")
                    )
                    if trade_status not in {
                        TradeStatus.OPEN.value,
                        TradeStatus.OPEN_OVERRIDE.value,
                    }:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return None

                    if active_breach is not None:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return None

                    pipe.multi()  # type: ignore[no-untyped-call]
                    pipe.hset(keyspace.breach_key(breach.id), mapping=mapping)
                    pipe.zadd(
                        keyspace.breaches_trade_key(trade_id),
                        {str(breach.id): self._datetime_to_epoch(breach.detected_at)},
                    )
                    pipe.sadd(keyspace.breaches_unresolved_key(), str(breach.id))
                    pipe.set(active_breach_key, str(breach.id))
                    await pipe.execute()
                    return breach
                except WatchError:
                    continue

    async def get_breach(self, breach_id: int) -> Breach | None:
        """Load a single breach by ID."""

        data = await self._resolve_redis_call(
            self._client.hgetall(keyspace.breach_key(breach_id))
        )
        if not data:
            return None
        return self._deserialize_breach(data)

    async def get_open_breach(self, trade_id: int) -> Breach | None:
        """Return the active unresolved breach for a trade, if any."""

        raw_breach_id = await self._resolve_redis_call(
            self._client.get(keyspace.breach_active_key(trade_id))
        )
        if raw_breach_id is None:
            return None
        breach_id = self._require_positive_int_from_redis(raw_breach_id)
        return await self.get_breach(breach_id)

    async def list_breaches_for_trade(self, trade_id: int) -> list[Breach]:
        """Return breaches for one trade ordered by detected_at ascending."""

        raw_ids = await self._resolve_redis_call(
            self._client.zrange(keyspace.breaches_trade_key(trade_id), 0, -1)
        )
        breaches: list[Breach] = []
        for raw_id in raw_ids:
            breach = await self.get_breach(
                self._require_positive_int_from_redis(raw_id)
            )
            if breach is not None:
                breaches.append(breach)
        return breaches

    async def list_unresolved_breaches(self) -> list[Breach]:
        """Return unresolved breaches ordered by detected_at ascending."""

        raw_ids = await self._resolve_redis_call(
            self._client.smembers(keyspace.breaches_unresolved_key())
        )
        breaches: list[Breach] = []
        for raw_id in raw_ids:
            breach = await self.get_breach(
                self._require_positive_int_from_redis(raw_id)
            )
            if breach is not None and breach.user_response is None:
                breaches.append(breach)
        return sorted(breaches, key=lambda breach: breach.detected_at)

    async def resolve_breach(
        self,
        breach_id: int,
        *,
        user_response: BreachUserResponse,
        response_at: datetime,
        justification: str | None = None,
    ) -> Breach | None:
        """Resolve an active breach without modifying trade state."""

        breach = await self.get_breach(breach_id)
        if breach is None:
            return None

        result = await self._eval_script(
            "resolve_breach",
            keys=[
                keyspace.breach_key(breach_id),
                keyspace.breaches_unresolved_key(),
                keyspace.breach_active_key(breach.trade_id),
            ],
            args=[
                str(breach_id),
                user_response.value,
                self._serialize_datetime(response_at),
                justification or "",
            ],
        )

        if int(result) != 1:
            return None
        return await self.get_breach(breach_id)

    async def record_alert(
        self,
        breach_id: int,
        *,
        sent_at: datetime,
        escalation_level: int,
        message: str,
    ) -> Alert:
        """Persist an alert and index it by breach."""

        alert_id = await self._client.incr(keyspace.alert_id_sequence_key())
        alert = Alert(
            id=alert_id,
            breach_id=breach_id,
            sent_at=self._ensure_aware_datetime(sent_at),
            escalation_level=escalation_level,
            message=message,
        )
        mapping = self._serialize_alert(alert)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(keyspace.alert_key(alert.id), mapping=mapping)
            pipe.zadd(
                keyspace.alerts_breach_key(breach_id),
                {str(alert.id): self._datetime_to_epoch(alert.sent_at)},
            )
            await pipe.execute()
        return alert

    async def get_conversation_state(self, chat_id: int) -> ConversationState | None:
        """Load the persisted conversation state for a chat."""

        data = await self._resolve_redis_call(
            self._client.hgetall(keyspace.conversation_key(chat_id))
        )
        if not data:
            return None
        return self._deserialize_conversation_state(data)

    async def set_conversation_state(
        self,
        state: ConversationState,
        *,
        ttl_seconds: int | None = None,
    ) -> ConversationState:
        """Persist conversation state and optional TTL atomically."""

        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(
                keyspace.conversation_key(state.chat_id),
                mapping=self._serialize_conversation_state(state),
            )
            if ttl_seconds is not None:
                pipe.expire(keyspace.conversation_key(state.chat_id), ttl_seconds)
            await pipe.execute()
        return state

    async def clear_conversation_state(self, chat_id: int) -> None:
        """Delete any in-progress conversation state."""

        await self._client.delete(keyspace.conversation_key(chat_id))

    async def recent_closed_trades(self, n: int) -> list[Trade]:
        """Return the most recent closed trades, newest first."""

        raw_ids = await self._resolve_redis_call(
            self._client.zrevrange(keyspace.trades_closed_key(), 0, n - 1)
        )
        trades: list[Trade] = []
        for raw_id in raw_ids:
            trade = await self.get_trade(self._require_positive_int_from_redis(raw_id))
            if trade is not None:
                trades.append(trade)
        return trades

    async def list_closed_trades(self, limit: int | None = None) -> list[Trade]:
        """Return closed trades, newest first, with an optional result cap."""

        end_index = -1 if limit is None else limit - 1
        raw_ids = await self._resolve_redis_call(
            self._client.zrevrange(keyspace.trades_closed_key(), 0, end_index)
        )
        trades: list[Trade] = []
        for raw_id in raw_ids:
            trade = await self.get_trade(self._require_positive_int_from_redis(raw_id))
            if trade is not None:
                trades.append(trade)
        return trades

    async def consecutive_loss_count(self) -> int:
        """Count consecutive losing closed trades, ignoring breakevens."""

        raw_ids = await self._resolve_redis_call(
            self._client.zrevrange(keyspace.trades_closed_key(), 0, -1)
        )
        streak = 0
        for raw_id in raw_ids:
            trade = await self.get_trade(self._require_positive_int_from_redis(raw_id))
            if trade is None or trade.realized_pnl is None:
                continue
            if trade.realized_pnl < 0:
                streak += 1
                continue
            if trade.realized_pnl > 0:
                break
        return streak

    async def update_trade_realized_pnl(
        self,
        trade_id: int,
        *,
        realized_pnl: float,
    ) -> Trade | None:
        """Override realized P&L for an already-closed trade."""

        trade_key_name = keyspace.trade_key(trade_id)
        async with self._client.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(trade_key_name)
                    trade_data = await self._resolve_redis_call(
                        pipe.hgetall(trade_key_name)
                    )
                    if not trade_data:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return None

                    trade_status = self._as_text(
                        self._normalize_hash(trade_data).get("status")
                    )
                    if trade_status != TradeStatus.CLOSED.value:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return None

                    pipe.multi()  # type: ignore[no-untyped-call]
                    pipe.hset(
                        trade_key_name,
                        mapping={
                            "realized_pnl": self._serialize_float(realized_pnl),
                        },
                    )
                    await pipe.execute()
                    return await self.get_trade(trade_id)
                except WatchError:
                    continue

    async def update_trade(
        self,
        trade_id: int,
        updates: dict[str, Any],
    ) -> Trade:
        """Update editable fields on an open trade atomically.

        Validates that the trade exists and is in OPEN or OPEN_OVERRIDE status.
        Validates all provided fields using the same rules as /new command.
        Uses Redis WATCH/MULTI/EXEC for atomic updates.

        Args:
            trade_id: ID of the trade to update.
            updates: Dict of field names to new values.

        Returns:
            Updated Trade object.

        Raises:
            ValueError: If trade doesn't exist, is closed, or validation fails.
        """
        # Filter to only editable fields
        editable_updates = filter_editable_fields(updates)

        if not editable_updates:
            # Nothing to update - just return current trade
            trade = await self.get_trade(trade_id)
            if trade is None:
                msg = f"Trade {trade_id} not found."
                raise ValueError(msg)
            return trade

        trade_key_name = keyspace.trade_key(trade_id)

        async with self._client.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(trade_key_name)
                    trade_data = await self._resolve_redis_call(
                        pipe.hgetall(trade_key_name)
                    )
                    if not trade_data:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        msg = f"Trade {trade_id} not found."
                        raise ValueError(msg)

                    # Deserialize to validate current state
                    existing_trade = self._deserialize_trade(trade_data)

                    # Validate trade is open
                    if existing_trade.status not in {
                        TradeStatus.OPEN,
                        TradeStatus.OPEN_OVERRIDE,
                    }:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        msg = (
                            f"Trade {trade_id} is not open. "
                            "Only open trades can be edited."
                        )
                        raise ValueError(msg)

                    # Validate and apply each editable field
                    # Start with existing values, apply updates
                    updated_fields: dict[str, Any] = {
                        "direction": existing_trade.direction,
                        "size_usdt": existing_trade.size_usdt,
                        "leverage": existing_trade.leverage,
                        "leverage_override_reason": existing_trade.leverage_override_reason,
                        "entry_price": existing_trade.entry_price,
                        "invalidation_price": existing_trade.invalidation_price,
                        "max_loss_usdt": existing_trade.max_loss_usdt,
                        "regime": existing_trade.regime,
                        "thesis": existing_trade.thesis,
                    }

                    # Apply validated updates
                    if "direction" in editable_updates:
                        updated_fields["direction"] = validate.validate_direction(
                            editable_updates["direction"]
                        )
                    if "size_usdt" in editable_updates:
                        updated_fields["size_usdt"] = validate.validate_size_usdt(
                            editable_updates["size_usdt"]
                        )
                    if "leverage" in editable_updates:
                        updated_fields["leverage"] = validate.validate_leverage(
                            editable_updates["leverage"]
                        )
                    if "leverage_override_reason" in editable_updates:
                        value = editable_updates["leverage_override_reason"]
                        if value is not None and value != "":
                            updated_fields["leverage_override_reason"] = (
                                validate.validate_leverage_override_reason(value)
                            )
                        else:
                            updated_fields["leverage_override_reason"] = None
                    if "entry_price" in editable_updates:
                        updated_fields["entry_price"] = validate.validate_entry_price(
                            editable_updates["entry_price"]
                        )
                    if "invalidation_price" in editable_updates:
                        updated_fields["invalidation_price"] = (
                            validate.validate_invalidation_price(
                                editable_updates["invalidation_price"]
                            )
                        )
                    if "max_loss_usdt" in editable_updates:
                        updated_fields["max_loss_usdt"] = validate.validate_max_loss_usdt(
                            editable_updates["max_loss_usdt"]
                        )
                    if "regime" in editable_updates:
                        updated_fields["regime"] = validate.validate_regime(
                            editable_updates["regime"]
                        )
                    if "thesis" in editable_updates:
                        updated_fields["thesis"] = validate.validate_thesis(
                            editable_updates["thesis"]
                        )

                    # Validate invalidation price side
                    validate.validate_invalidation_side(
                        direction=updated_fields["direction"],
                        entry_price=updated_fields["entry_price"],
                        invalidation_price=updated_fields["invalidation_price"],
                    )

                    # Handle leverage_override_reason based on leverage changes
                    # If leverage was reduced below 20, clear the override reason
                    original_leverage = existing_trade.leverage
                    new_leverage = updated_fields["leverage"]

                    if original_leverage >= 20 and new_leverage < 20:
                        # Leverage reduced below 20 - clear override reason
                        updated_fields["leverage_override_reason"] = None
                    elif "leverage" not in editable_updates:
                        # Leverage unchanged - preserve existing override reason
                        updated_fields["leverage_override_reason"] = (
                            existing_trade.leverage_override_reason
                        )
                    # If leverage was explicitly set (even if same value), keep the provided override reason

                    # If leverage >= 20 and override reason not provided, require it
                    if (
                        new_leverage >= 20
                        and updated_fields["leverage_override_reason"] is None
                        and (
                            "leverage_override_reason" not in editable_updates
                            or editable_updates["leverage_override_reason"] is None
                            or editable_updates["leverage_override_reason"] == ""
                        )
                    ):
                        # Check if we should preserve existing reason (leverage unchanged)
                        if "leverage" not in editable_updates:
                            updated_fields["leverage_override_reason"] = (
                                existing_trade.leverage_override_reason
                            )
                        else:
                            msg = (
                                "leverage_override_reason is required when leverage >= 20. "
                                "Must be 10-500 characters."
                            )
                            raise ValueError(msg)

                    # Build update mapping
                    update_mapping: dict[str, str] = {}

                    # Only add fields that changed
                    for field_name in EDITABLE_FIELDS:
                        if field_name in updated_fields:
                            value = updated_fields[field_name]
                            if field_name == "direction":
                                update_mapping[field_name] = value.value
                            elif field_name == "regime":
                                update_mapping[field_name] = value.value
                            elif field_name == "leverage":
                                update_mapping[field_name] = str(value)
                            elif field_name in {
                                "size_usdt",
                                "entry_price",
                                "invalidation_price",
                                "max_loss_usdt",
                            }:
                                update_mapping[field_name] = self._serialize_float(value)
                            elif field_name == "leverage_override_reason":
                                if value is not None:
                                    update_mapping[field_name] = value
                                # If None, don't include in mapping (will be cleared/deleted)
                            elif field_name == "thesis":
                                update_mapping[field_name] = value

                    # If leverage_override_reason is being set to None explicitly, we need to delete it
                    if (
                        "leverage_override_reason" in editable_updates
                        and editable_updates["leverage_override_reason"] is None
                    ) or (
                        updated_fields["leverage_override_reason"] is None
                        and existing_trade.leverage_override_reason is not None
                        and "leverage_override_reason" not in editable_updates
                        and original_leverage >= 20
                        and new_leverage < 20
                    ):
                        # Delete the field from Redis
                        pass  # Will handle below

                    pipe.multi()  # type: ignore[no-untyped-call]
                    if update_mapping:
                        pipe.hset(trade_key_name, mapping=update_mapping)

                    # If leverage_override_reason should be cleared/deleted
                    if updated_fields["leverage_override_reason"] is None and existing_trade.leverage_override_reason is not None:
                        if "leverage_override_reason" not in update_mapping:
                            pipe.hdel(trade_key_name, "leverage_override_reason")

                    await pipe.execute()
                    return await self.get_trade(trade_id)
                except WatchError:
                    continue

    async def list_active_signals(self) -> list[Signal]:
        """Return non-expired active signals from the reserved namespace."""

        now = datetime.now(tz=UTC)
        raw_ids = await self._resolve_redis_call(
            self._client.zrange(keyspace.signals_active_key(), 0, -1)
        )
        signals: list[Signal] = []
        for raw_id in raw_ids:
            signal = await self.get_signal(
                self._require_positive_int_from_redis(raw_id)
            )
            if signal is None:
                continue
            if signal.expires_at is not None and signal.expires_at <= now:
                continue
            signals.append(signal)
        return signals

    async def insert_signal(
        self,
        *,
        source: str,
        kind: str,
        severity: Severity,
        detected_at: datetime,
        expires_at: datetime | None,
        payload_json: str,
        summary: str,
    ) -> Signal:
        """Insert a reserved v2 signal record atomically."""

        signal_id = await self._client.incr(keyspace.signal_id_sequence_key())
        signal = Signal(
            id=signal_id,
            source=source,
            kind=kind,
            severity=severity,
            detected_at=self._ensure_aware_datetime(detected_at),
            expires_at=(
                self._ensure_aware_datetime(expires_at)
                if expires_at is not None
                else None
            ),
            payload_json=payload_json,
            summary=summary,
        )
        mapping = self._serialize_signal(signal)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(keyspace.signal_key(signal.id), mapping=mapping)
            pipe.zadd(
                keyspace.signals_active_key(),
                {str(signal.id): self._datetime_to_epoch(signal.detected_at)},
            )
            await pipe.execute()
        return signal

    async def get_signal(self, signal_id: int) -> Signal | None:
        """Load a single reserved signal by ID."""

        data = await self._resolve_redis_call(
            self._client.hgetall(keyspace.signal_key(signal_id))
        )
        if not data:
            return None
        return self._deserialize_signal(data)

    async def _eval_script(
        self,
        script_name: str,
        *,
        keys: list[str],
        args: list[str],
    ) -> Any:
        script = self._scripts[script_name]
        return await self._resolve_redis_call(
            self._client.eval(script, len(keys), *keys, *args)
        )

    async def _resolve_redis_call(self, value: Awaitable[T] | T) -> T:
        if inspect.isawaitable(value):
            return await cast(Awaitable[T], value)
        return value

    def _trade_from_draft(
        self,
        *,
        trade_id: int,
        draft: TradeDraft,
        opened_at: datetime,
        status: TradeStatus,
        size_reduction_enforced: bool | None,
    ) -> Trade:
        return Trade(
            id=trade_id,
            direction=self._require(draft.direction, "direction"),
            size_usdt=self._require(draft.size_usdt, "size_usdt"),
            leverage=self._require(draft.leverage, "leverage"),
            leverage_override_reason=draft.leverage_override_reason,
            entry_price=self._require(draft.entry_price, "entry_price"),
            invalidation_price=self._require(
                draft.invalidation_price,
                "invalidation_price",
            ),
            max_loss_usdt=self._require(draft.max_loss_usdt, "max_loss_usdt"),
            regime=self._require(draft.regime, "regime"),
            thesis=self._require(draft.thesis, "thesis"),
            status=status,
            size_reduction_enforced=(
                draft.size_reduction_enforced
                if size_reduction_enforced is None
                else size_reduction_enforced
            ),
            opened_at=self._ensure_aware_datetime(opened_at),
            closed_at=None,
            close_price=None,
            realized_pnl=None,
        )

    @staticmethod
    def _require(value: T | None, field_name: str) -> T:
        if value is None:
            msg = f"TradeDraft field {field_name} is required."
            raise ValueError(msg)
        return value

    @staticmethod
    def _ensure_aware_datetime(value: datetime | None) -> datetime:
        if value is None:
            msg = "datetime value is required."
            raise ValueError(msg)
        if value.tzinfo is None:
            msg = "datetime value must be timezone-aware."
            raise ValueError(msg)
        return value

    @staticmethod
    def _datetime_to_epoch(value: datetime) -> float:
        return RedisRepository._ensure_aware_datetime(value).timestamp()

    @staticmethod
    def _serialize_epoch(value: datetime) -> str:
        epoch = RedisRepository._datetime_to_epoch(value)
        return RedisRepository._serialize_float(epoch)

    @staticmethod
    def _serialize_datetime(value: datetime) -> str:
        return RedisRepository._ensure_aware_datetime(value).isoformat()

    @staticmethod
    def _serialize_float(value: float) -> str:
        return format(value, ".17g")

    @staticmethod
    def _serialize_bool(value: bool) -> str:
        return "1" if value else "0"

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        normalized = RedisRepository._as_text(value)
        return normalized in {"1", "true", "True"}

    @staticmethod
    def _as_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    @staticmethod
    def _require_positive_int_from_redis(value: Any) -> int:
        normalized = RedisRepository._as_text(value)
        if normalized is None:
            msg = "Redis value is missing."
            raise ValueError(msg)
        parsed = int(normalized)
        if parsed <= 0:
            msg = f"Expected positive integer from Redis, got {normalized!r}."
            raise ValueError(msg)
        return parsed

    @staticmethod
    def _normalize_hash(raw_mapping: dict[Any, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in raw_mapping.items():
            key = RedisRepository._as_text(raw_key)
            value = RedisRepository._as_text(raw_value)
            if key is None or value is None:
                continue
            normalized[key] = value
        return normalized

    @staticmethod
    def _parse_appendonly_enabled(
        config_value: Any,
        info_value: Any,
    ) -> bool | None:
        config_text = RedisRepository._as_text(config_value)
        if config_text is not None:
            return config_text.lower() == "yes"

        info_text = RedisRepository._as_text(info_value)
        if info_text is None:
            return None
        return info_text in {"1", "true", "True"}

    @staticmethod
    def _calculate_realized_pnl(trade: Trade, close_price: float) -> float:
        size_btc = trade.size_usdt / trade.entry_price
        direction_sign = 1.0 if trade.direction == Direction.LONG else -1.0
        return (close_price - trade.entry_price) * size_btc * direction_sign

    def _serialize_trade(self, trade: Trade) -> dict[str, str]:
        mapping: dict[str, str] = {
            "id": str(trade.id),
            "direction": trade.direction.value,
            "size_usdt": self._serialize_float(trade.size_usdt),
            "leverage": str(trade.leverage),
            "entry_price": self._serialize_float(trade.entry_price),
            "invalidation_price": self._serialize_float(trade.invalidation_price),
            "max_loss_usdt": self._serialize_float(trade.max_loss_usdt),
            "regime": trade.regime.value,
            "thesis": trade.thesis,
            "status": trade.status.value,
            "size_reduction_enforced": self._serialize_bool(
                trade.size_reduction_enforced
            ),
            "opened_at": self._serialize_datetime(trade.opened_at),
        }
        if trade.leverage_override_reason is not None:
            mapping["leverage_override_reason"] = trade.leverage_override_reason
        if trade.closed_at is not None:
            mapping["closed_at"] = self._serialize_datetime(trade.closed_at)
        if trade.close_price is not None:
            mapping["close_price"] = self._serialize_float(trade.close_price)
        if trade.realized_pnl is not None:
            mapping["realized_pnl"] = self._serialize_float(trade.realized_pnl)
        return mapping

    def _deserialize_trade(self, raw_mapping: dict[Any, Any]) -> Trade:
        data = self._normalize_hash(raw_mapping)
        payload: dict[str, Any] = {
            "id": int(data["id"]),
            "direction": data["direction"],
            "size_usdt": float(data["size_usdt"]),
            "leverage": int(data["leverage"]),
            "leverage_override_reason": data.get("leverage_override_reason"),
            "entry_price": float(data["entry_price"]),
            "invalidation_price": float(data["invalidation_price"]),
            "max_loss_usdt": float(data["max_loss_usdt"]),
            "regime": data["regime"],
            "thesis": data["thesis"],
            "status": data["status"],
            "size_reduction_enforced": self._parse_bool(
                data["size_reduction_enforced"]
            ),
            "opened_at": datetime.fromisoformat(data["opened_at"]),
            "closed_at": (
                datetime.fromisoformat(data["closed_at"])
                if "closed_at" in data
                else None
            ),
            "close_price": (
                float(data["close_price"]) if "close_price" in data else None
            ),
            "realized_pnl": (
                float(data["realized_pnl"]) if "realized_pnl" in data else None
            ),
        }
        return Trade.model_validate(payload)

    def _serialize_breach(self, breach: Breach) -> dict[str, str]:
        mapping: dict[str, str] = {
            "id": str(breach.id),
            "trade_id": str(breach.trade_id),
            "detected_at": self._serialize_datetime(breach.detected_at),
            "breach_price": self._serialize_float(breach.breach_price),
        }
        if breach.user_response is not None:
            mapping["user_response"] = breach.user_response.value
        if breach.response_at is not None:
            mapping["response_at"] = self._serialize_datetime(breach.response_at)
        if breach.justification is not None:
            mapping["justification"] = breach.justification
        return mapping

    def _deserialize_breach(self, raw_mapping: dict[Any, Any]) -> Breach:
        data = self._normalize_hash(raw_mapping)
        payload: dict[str, Any] = {
            "id": int(data["id"]),
            "trade_id": int(data["trade_id"]),
            "detected_at": datetime.fromisoformat(data["detected_at"]),
            "breach_price": float(data["breach_price"]),
            "user_response": data.get("user_response"),
            "response_at": (
                datetime.fromisoformat(data["response_at"])
                if "response_at" in data
                else None
            ),
            "justification": data.get("justification"),
        }
        return Breach.model_validate(payload)

    def _serialize_alert(self, alert: Alert) -> dict[str, str]:
        return {
            "id": str(alert.id),
            "breach_id": str(alert.breach_id),
            "sent_at": self._serialize_datetime(alert.sent_at),
            "escalation_level": str(alert.escalation_level),
            "message": alert.message,
        }

    def _deserialize_alert(self, raw_mapping: dict[Any, Any]) -> Alert:
        data = self._normalize_hash(raw_mapping)
        payload = {
            "id": int(data["id"]),
            "breach_id": int(data["breach_id"]),
            "sent_at": datetime.fromisoformat(data["sent_at"]),
            "escalation_level": int(data["escalation_level"]),
            "message": data["message"],
        }
        return Alert.model_validate(payload)

    def _serialize_conversation_state(
        self,
        state: ConversationState,
    ) -> dict[str, str]:
        return {
            "chat_id": str(state.chat_id),
            "state": state.state.value,
            "partial_trade_json": state.partial_trade_json,
            "updated_at": self._serialize_datetime(state.updated_at),
        }

    def _deserialize_conversation_state(
        self,
        raw_mapping: dict[Any, Any],
    ) -> ConversationState:
        data = self._normalize_hash(raw_mapping)
        payload = {
            "chat_id": int(data["chat_id"]),
            "state": data["state"],
            "partial_trade_json": data["partial_trade_json"],
            "updated_at": datetime.fromisoformat(data["updated_at"]),
        }
        return ConversationState.model_validate(payload)

    def _serialize_signal(self, signal: Signal) -> dict[str, str]:
        mapping: dict[str, str] = {
            "id": str(signal.id),
            "source": signal.source,
            "kind": signal.kind,
            "severity": signal.severity.value,
            "detected_at": self._serialize_datetime(signal.detected_at),
            "payload_json": signal.payload_json,
            "summary": signal.summary,
        }
        if signal.expires_at is not None:
            mapping["expires_at"] = self._serialize_datetime(signal.expires_at)
        return mapping

    def _deserialize_signal(self, raw_mapping: dict[Any, Any]) -> Signal:
        data = self._normalize_hash(raw_mapping)
        payload = {
            "id": int(data["id"]),
            "source": data["source"],
            "kind": data["kind"],
            "severity": data["severity"],
            "detected_at": datetime.fromisoformat(data["detected_at"]),
            "expires_at": (
                datetime.fromisoformat(data["expires_at"])
                if "expires_at" in data
                else None
            ),
            "payload_json": data["payload_json"],
            "summary": data["summary"],
        }
        return Signal.model_validate(payload)
