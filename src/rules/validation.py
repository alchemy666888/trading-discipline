"""Pure field validators used by the Telegram form flow."""

from __future__ import annotations

from src.models.trade import Direction, Regime


def validate_direction(value: object) -> Direction:
    """Validate the trade direction field."""

    if isinstance(value, Direction):
        return value
    if not isinstance(value, str) or not value.strip():
        msg = "direction must be 'long' or 'short'. Example: long."
        raise ValueError(msg)
    normalized = value.strip()
    if normalized == Direction.LONG.value:
        return Direction.LONG
    if normalized == Direction.SHORT.value:
        return Direction.SHORT
    msg = "direction must be 'long' or 'short'. Example: long."
    raise ValueError(msg)


def validate_size_usdt(value: object) -> float:
    """Validate a positive trade size in USDT."""

    return _parse_positive_float(
        "size_usdt",
        value,
        example="5000",
    )


def validate_leverage(value: object) -> int:
    """Validate an integer leverage value in the supported exchange range."""

    try:
        normalized = _coerce_text_or_number(value)
    except ValueError as exc:
        msg = "leverage must be an integer between 1 and 125. Example: 10."
        raise ValueError(msg) from exc
    if isinstance(normalized, int):
        leverage = normalized
    elif isinstance(normalized, float):
        if not normalized.is_integer():
            msg = "leverage must be an integer between 1 and 125. Example: 10."
            raise ValueError(msg)
        leverage = int(normalized)
    else:
        try:
            leverage = int(normalized)
        except ValueError as exc:
            msg = "leverage must be an integer between 1 and 125. Example: 10."
            raise ValueError(msg) from exc
        if str(leverage) != normalized:
            msg = "leverage must be an integer between 1 and 125. Example: 10."
            raise ValueError(msg)

    if leverage < 1 or leverage > 125:
        msg = "leverage must be an integer between 1 and 125. Example: 10."
        raise ValueError(msg)
    return leverage


def validate_entry_price(value: object) -> float:
    """Validate a positive entry price."""

    return _parse_positive_float(
        "entry_price",
        value,
        example="82500",
    )


def validate_invalidation_price(value: object) -> float:
    """Validate a positive invalidation price."""

    return _parse_positive_float(
        "invalidation_price",
        value,
        example="81200",
    )


def validate_invalidation_side(
    *,
    direction: Direction,
    entry_price: float,
    invalidation_price: float,
) -> None:
    """Validate that invalidation is on the correct side of entry."""

    if direction == Direction.LONG and invalidation_price >= entry_price:
        msg = (
            "invalidation_price must be less than entry_price for long trades. "
            "Example: entry 82500, invalidation 81200."
        )
        raise ValueError(msg)
    if direction == Direction.SHORT and invalidation_price <= entry_price:
        msg = (
            "invalidation_price must be greater than entry_price for short trades. "
            "Example: entry 82500, invalidation 83800."
        )
        raise ValueError(msg)


def validate_max_loss_usdt(value: object) -> float:
    """Validate a positive max-loss field."""

    return _parse_positive_float(
        "max_loss_usdt",
        value,
        example="160",
    )


def validate_regime(value: object) -> Regime:
    """Validate the allowed regime labels."""

    if isinstance(value, Regime):
        return value
    if not isinstance(value, str) or not value.strip():
        msg = (
            "regime must be one of: uptrend, range, downtrend, event_risk. "
            "Example: range."
        )
        raise ValueError(msg)
    normalized = value.strip()
    for regime in Regime:
        if normalized == regime.value:
            return regime
    msg = (
        "regime must be one of: uptrend, range, downtrend, event_risk. "
        "Example: range."
    )
    raise ValueError(msg)


def validate_thesis(value: object) -> str:
    """Validate thesis length and non-empty content."""

    if not isinstance(value, str):
        msg = (
            "thesis must be 10 to 280 characters. "
            "Example: Holding above 82K with continuation setup."
        )
        raise ValueError(msg)
    thesis = value.strip()
    if len(thesis) < 10 or len(thesis) > 280:
        msg = (
            "thesis must be 10 to 280 characters. "
            "Example: Holding above 82K with continuation setup."
        )
        raise ValueError(msg)
    return thesis


def validate_leverage_override_reason(value: object) -> str:
    """Validate the high-leverage override reason text."""

    if not isinstance(value, str):
        msg = (
            "leverage_override_reason must be 10 to 500 characters. "
            "Example: Tight stop around a defined event."
        )
        raise ValueError(msg)
    reason = value.strip()
    if len(reason) < 10 or len(reason) > 500:
        msg = (
            "leverage_override_reason must be 10 to 500 characters. "
            "Example: Tight stop around a defined event."
        )
        raise ValueError(msg)
    return reason


def validate_justification(value: object) -> str:
    """Validate breach-justification free text."""

    if not isinstance(value, str):
        msg = (
            "justification must be 5 to 500 characters. "
            "Example: Breakout retest is still holding."
        )
        raise ValueError(msg)
    justification = value.strip()
    if len(justification) < 5 or len(justification) > 500:
        msg = (
            "justification must be 5 to 500 characters. "
            "Example: Breakout retest is still holding."
        )
        raise ValueError(msg)
    return justification


def _parse_positive_float(field_name: str, value: object, *, example: str) -> float:
    try:
        normalized = _coerce_text_or_number(value)
    except ValueError as exc:
        msg = f"{field_name} must be greater than 0. Example: {example}."
        raise ValueError(msg) from exc
    try:
        number = float(normalized)
    except (TypeError, ValueError) as exc:
        msg = f"{field_name} must be greater than 0. Example: {example}."
        raise ValueError(msg) from exc

    if number <= 0:
        msg = f"{field_name} must be greater than 0. Example: {example}."
        raise ValueError(msg)
    return number


def _coerce_text_or_number(value: object) -> str | float | int:
    if isinstance(value, bool):
        msg = "boolean values are not valid numeric input."
        raise ValueError(msg)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            msg = "value must not be empty."
            raise ValueError(msg)
        return normalized
    msg = "value must be text or a number."
    raise ValueError(msg)
