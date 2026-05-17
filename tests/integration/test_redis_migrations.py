"""Integration tests for Redis keyspace bootstrap and versioning."""

from __future__ import annotations

from pathlib import Path

from redis.asyncio import Redis

from src.db import keyspace, migrations


async def test_apply_migrations_sets_schema_version(redis_client: Redis) -> None:
    """REQ-011: Redis migration bootstrap sets and preserves schema:version."""

    version = await migrations.apply_migrations(redis_client)
    version_again = await migrations.apply_migrations(redis_client)

    assert version == keyspace.SCHEMA_VERSION
    assert version_again == keyspace.SCHEMA_VERSION
    assert await redis_client.get(keyspace.schema_version_key()) == str(
        keyspace.SCHEMA_VERSION
    )


async def test_migrations_leave_signal_namespace_empty(
    redis_client: Redis,
) -> None:
    """REQ-010: migrations create the signal contract without creating signal keys."""

    await migrations.apply_migrations(redis_client)

    assert await redis_client.exists(keyspace.signals_active_key()) == 0
    assert await redis_client.keys("signals:*") == []


async def test_migrations_do_not_create_sqlite_artifacts(
    redis_client: Redis,
    redis_container: object,
) -> None:
    """REQ-011: Redis bootstrap does not create or require a SQLite database file."""

    await migrations.apply_migrations(redis_client)
    data_dir = redis_container.data_dir

    assert isinstance(data_dir, Path)
    assert list(data_dir.rglob("*.db")) == []
