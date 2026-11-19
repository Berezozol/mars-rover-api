"""`ImageDiskCache` (stage 9a, app.services.mer_images).

Contract: ``ImageDiskCache(directory, max_bytes)``; ``get(key) -> bytes | None``,
``put(key, data)``; when the total size exceeds ``max_bytes`` the least recently
requested entries are evicted; writes are atomic (a reader never sees half a file); disk
errors never propagate out of ``get`` / ``put``.
"""

import os
import threading
import time
from pathlib import Path

import pytest

from app.services.mer_images import ImageDiskCache

pytestmark = pytest.mark.unit


def _files(directory: Path) -> list[Path]:
    return [p for p in directory.rglob("*") if p.is_file()]


def _total(directory: Path) -> int:
    return sum(p.stat().st_size for p in _files(directory))


def test_get_missing_key_returns_none(tmp_path: Path) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=1000)

    assert cache.get("nope") is None


def test_put_then_get_returns_the_same_bytes(tmp_path: Path) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=1000)

    cache.put("photo-1", b"\xff\xd8jpeg\x00bytes")

    assert cache.get("photo-1") == b"\xff\xd8jpeg\x00bytes"


def test_put_creates_a_missing_directory(tmp_path: Path) -> None:
    directory = tmp_path / "a" / "b" / "cache"
    cache = ImageDiskCache(directory, max_bytes=1000)

    cache.put("k", b"data")

    assert cache.get("k") == b"data"
    assert directory.is_dir()


def test_put_same_key_overwrites_the_value(tmp_path: Path) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=1000)

    cache.put("k", b"old-value")
    cache.put("k", b"new")

    assert cache.get("k") == b"new"
    assert len(_files(tmp_path / "cache")) == 1


def test_entries_survive_a_new_cache_object_on_the_same_directory(tmp_path: Path) -> None:
    ImageDiskCache(tmp_path / "cache", max_bytes=1000).put("k", b"persisted")

    assert ImageDiskCache(tmp_path / "cache", max_bytes=1000).get("k") == b"persisted"


def test_keys_do_not_collide(tmp_path: Path) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=10_000)
    # Cache keys are ids like "123-full"; NTFS is case-insensitive, so no case-only pairs.
    keys = ["1", "11", "1-full", "11-full", "photo-1", "photo-11"]

    for index, key in enumerate(keys):
        cache.put(key, f"value-{index}".encode())

    for index, key in enumerate(keys):
        assert cache.get(key) == f"value-{index}".encode()


def _set_age(directory: Path, key: str, seconds_ago: float) -> None:
    """Pin the "last used" time of one entry (the file mtime): coarse clocks make ties."""
    stamp = time.time() - seconds_ago
    [path] = [p for p in _files(directory) if p.stem == key or p.name.startswith(key + ".")]
    os.utime(path, (stamp, stamp))


def test_eviction_removes_the_least_recently_requested_entry(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=300)
    for key in ("a", "b", "c"):
        cache.put(key, key.encode() * 100)
    assert cache.get("a") is not None  # "a" and "c" were requested after "b"
    assert cache.get("c") is not None
    _set_age(directory, "b", 200)
    _set_age(directory, "a", 50)
    _set_age(directory, "c", 40)

    cache.put("d", b"d" * 100)  # 400 bytes > 300: one entry has to go

    assert cache.get("b") is None  # the oldest request
    assert cache.get("a") == b"a" * 100
    assert cache.get("c") == b"c" * 100
    assert cache.get("d") == b"d" * 100


def test_get_refreshes_recency_so_a_read_entry_outlives_an_unread_one(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=200)
    cache.put("old", b"o" * 100)
    cache.put("new", b"n" * 100)
    _set_age(directory, "old", 500)
    _set_age(directory, "new", 400)  # "old" is older by the clock...
    assert cache.get("old") is not None  # ...but it is requested now

    cache.put("third", b"t" * 100)

    assert cache.get("new") is None
    assert cache.get("old") == b"o" * 100


def test_eviction_keeps_the_total_size_within_the_limit(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=1000)

    for index in range(30):
        cache.put(f"k{index}", bytes([index]) * 100)

    assert _total(directory) <= 1000
    assert cache.get("k29") == bytes([29]) * 100  # the newest always stays


def test_eviction_frees_enough_for_one_big_entry(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=500)
    for key in "abcde":
        cache.put(key, key.encode() * 100)

    cache.put("big", b"B" * 350)

    assert _total(directory) <= 500
    assert cache.get("big") == b"B" * 350


def test_entry_larger_than_the_limit_does_not_raise_or_break_the_cache(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=100)
    cache.put("small", b"s" * 50)

    cache.put("huge", b"H" * 1000)

    # Whether an entry bigger than the whole limit is kept is up to the cache; it must only
    # stay consistent.
    assert cache.get("huge") in (None, b"H" * 1000)
    assert cache.get("small") in (None, b"s" * 50)


def test_put_leaves_no_temporary_files(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=10_000)

    for key in ("a", "b", "c"):
        cache.put(key, b"x" * 10)

    assert len(_files(directory)) == 3
    assert sum(p.stat().st_size for p in _files(directory)) == 30


def test_concurrent_readers_never_see_a_partial_entry(tmp_path: Path) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=10_000_000)
    first, second = b"A" * 500_000, b"B" * 700_000
    cache.put("k", first)
    stop = threading.Event()
    seen: list[bytes | None] = []
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for index in range(60):
                cache.put("k", second if index % 2 else first)
        except BaseException as exc:  # noqa: BLE001 - reported through the assertion below
            errors.append(exc)
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                seen.append(cache.get("k"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors
    assert seen, "readers did not run"
    # Never half a file or an empty one. None is tolerated: on Windows a reader that holds
    # the file open makes the writer's replace fail (the cache swallows it and logs).
    assert all(value in (first, second, None) for value in seen)
    assert any(value is not None for value in seen)
    assert cache.get("k") in (first, second)


def test_put_failure_while_writing_is_swallowed_and_leaves_old_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=1000)
    cache.put("k", b"old")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    cache.put("k", b"new")  # must not raise

    monkeypatch.undo()
    assert cache.get("k") == b"old"
    assert len(_files(tmp_path / "cache")) == 1  # the temporary file was cleaned up


def test_put_and_get_with_an_unusable_directory_do_not_raise(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=1000)
    cache.put("k", b"value")
    for path in _files(directory):
        path.unlink()
    directory.rmdir()
    directory.write_bytes(b"i am a file, not a directory")

    cache.put("k2", b"value")  # must not raise
    assert cache.get("k") is None
    assert cache.get("k2") is None


def test_get_unreadable_entry_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ImageDiskCache(tmp_path / "cache", max_bytes=1000)
    cache.put("k", b"value")

    def boom(*args: object, **kwargs: object) -> bytes:
        raise OSError("i/o error")

    monkeypatch.setattr(Path, "read_bytes", boom)
    monkeypatch.setattr(Path, "open", boom)

    assert cache.get("k") is None


def test_key_cannot_escape_the_cache_directory(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = ImageDiskCache(directory, max_bytes=1000)

    cache.put("../escaped", b"evil")
    cache.put("..\\escaped2", b"evil")
    cache.put("/abs/escaped3", b"evil")

    outside = [p for p in tmp_path.rglob("*") if p.is_file() and directory not in p.parents]
    assert outside == []
