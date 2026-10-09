from datetime import UTC, datetime, timedelta

import pytest

from kenya_data_engine.cache import Cache


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "engine.db")


def test_put_get_roundtrip(cache):
    cache.put("k", b"hello", "text/plain", 1)
    entry = cache.get("k")
    assert entry is not None
    assert entry.content == b"hello" and entry.content_type == "text/plain"
    assert entry.key == "k"


def test_missing_is_none(cache):
    assert cache.get("nope") is None


def test_expired_entry_is_miss(cache):
    cache.put("k", b"x", "text/plain", 1)
    assert cache.get("k", now=datetime.now(UTC) + timedelta(hours=2)) is None
    assert cache.get("k", now=datetime.now(UTC) + timedelta(minutes=30)) is not None


def test_put_overwrites(cache):
    cache.put("k", b"a", "text/plain", 1)
    cache.put("k", b"b", "text/html", 1)
    entry = cache.get("k")
    assert entry is not None and entry.content == b"b" and entry.content_type == "text/html"


def test_persists_across_instances(tmp_path):
    Cache(tmp_path / "e.db").put("k", b"a", "t", 1)
    assert Cache(tmp_path / "e.db").get("k") is not None
