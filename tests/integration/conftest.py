"""Integration fixtures backed by a disposable Redis Docker container."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.db.repo import RedisRepository


@dataclass(frozen=True)
class RedisContainer:
    """Disposable Redis container metadata for integration tests."""

    name: str
    url: str
    data_dir: Path


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        check=False,
        text=True,
    )


def _ensure_docker_available() -> None:
    result = _docker("info")
    if result.returncode != 0:
        pytest.skip("Docker daemon is required for Redis-backed integration tests.")


async def _wait_for_redis(redis_url: str) -> None:
    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        for _ in range(60):
            try:
                await client.ping()
                return
            except Exception:
                await asyncio.sleep(0.25)
    finally:
        await client.aclose()

    msg = f"Redis container at {redis_url} did not become ready."
    raise RuntimeError(msg)


async def _start_redis_container(
    tmp_path: Path,
    *,
    appendonly: str,
) -> RedisContainer:
    _ensure_docker_available()
    port = _free_tcp_port()
    name = f"btc-discipline-test-{uuid.uuid4().hex[:10]}"
    data_dir = tmp_path / name
    data_dir.mkdir(parents=True, exist_ok=True)

    result = _docker(
        "run",
        "--rm",
        "--detach",
        "--name",
        name,
        "--publish",
        f"{port}:6379",
        "--volume",
        f"{data_dir}:/data",
        "redis:7-alpine",
        "redis-server",
        "--appendonly",
        appendonly,
        "--dir",
        "/data",
    )
    if result.returncode != 0:
        msg = f"Failed to start Redis test container: {result.stderr.strip()}"
        raise RuntimeError(msg)

    container = RedisContainer(
        name=name,
        url=f"redis://127.0.0.1:{port}/0",
        data_dir=data_dir,
    )
    await _wait_for_redis(container.url)
    return container


async def _stop_redis_container(container: RedisContainer) -> None:
    _docker("rm", "--force", container.name)


@pytest_asyncio.fixture
async def redis_container(tmp_path: Path) -> RedisContainer:
    container = await _start_redis_container(tmp_path, appendonly="yes")
    try:
        yield container
    finally:
        await _stop_redis_container(container)


@pytest_asyncio.fixture
async def redis_no_aof_container(tmp_path: Path) -> RedisContainer:
    container = await _start_redis_container(tmp_path, appendonly="no")
    try:
        yield container
    finally:
        await _stop_redis_container(container)


@pytest_asyncio.fixture
async def redis_client(redis_container: RedisContainer) -> Redis:
    client = Redis.from_url(redis_container.url, decode_responses=True)
    try:
        yield client
    finally:
        signal_keys = await client.keys("signals:*")
        assert signal_keys == []
        await client.aclose()


@pytest_asyncio.fixture
async def redis_repo(redis_client: Redis) -> RedisRepository:
    repo = RedisRepository(redis_client)
    await repo.apply_migrations()
    yield repo
