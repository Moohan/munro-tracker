from __future__ import annotations

from unittest.mock import patch

from app.infrastructure.redis_client import get_redis


def test_get_redis_returns_cached_client() -> None:
    get_redis.cache_clear()
    with patch("app.infrastructure.redis_client.Redis.from_url") as mock_from_url:
        mock_from_url.return_value = object()

        first = get_redis()
        second = get_redis()

    assert first is second
    mock_from_url.assert_called_once()
