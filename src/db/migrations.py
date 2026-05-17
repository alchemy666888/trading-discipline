"""Redis keyspace migration bootstrap."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from src.db import keyspace


class MigrationError(RuntimeError):
    """Raised when the Redis schema version is unsupported."""


MigrationFunc = Callable[[Redis], Awaitable[None]]


async def _migration_001_initialize_keyspace(redis_client: Redis) -> None:
    await redis_client.set(keyspace.schema_version_key(), str(keyspace.SCHEMA_VERSION))


MIGRATIONS: dict[int, MigrationFunc] = {
    1: _migration_001_initialize_keyspace,
}


async def apply_migrations(redis_client: Redis) -> int:
    """Apply forward-only datastore migrations and return the active version."""

    raw_version = await redis_client.get(keyspace.schema_version_key())
    current_version = int(raw_version) if raw_version is not None else 0

    if current_version > keyspace.SCHEMA_VERSION:
        msg = (
            "Redis schema version "
            f"{current_version} is newer than the supported version "
            f"{keyspace.SCHEMA_VERSION}."
        )
        raise MigrationError(msg)

    for target_version in sorted(MIGRATIONS):
        if current_version < target_version:
            await MIGRATIONS[target_version](redis_client)
            current_version = target_version

    return current_version
