"""Reading and resolving the API key of a request (docs/API.md, "Общие правила v2", stage 20).

``extract_api_key`` finds the key in a request scope; ``ApiKeyResolver`` maps a key to its
account with an in-memory cache; ``RateIdentity`` is what the rate-limit middleware decides
and the other mechanisms (response size charge, heavy-search gate) reuse, so all of them
count the same client.
"""

import asyncio
import functools
import ipaddress
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Scope

from app.core.apikeys import hash_api_key, is_our_key_format
from app.core.errors import is_legacy_path
from app.repositories import api_keys as keys_repo

API_KEY_HEADER = "X-Api-Key"
API_KEY_PARAM = "api_key"
IDENTITY_SCOPE_KEY = "mars.rate_identity"
UNKNOWN_CLIENT = "unknown"
DEFAULT_CACHE_SIZE = 10_000
DEFAULT_NEGATIVE_CACHE_SIZE = 1_024

KeySource = Literal["header", "query", ""]
KeyStatus = Literal["ok", "invalid", "revoked", "blocked"]


@dataclass(frozen=True)
class RateIdentity:
    """Who a request is counted as: an account (valid key) or a client address."""

    kind: Literal["ip", "account"]
    key: str
    account_id: int | None = None
    key_id: int | None = None


def extract_api_key(scope: Scope, *, legacy: bool | None = None) -> tuple[str | None, KeySource]:
    """The key of the request and where it came from.

    The ``X-Api-Key`` header wins (a blank one counts as absent); its value is returned as is,
    whatever its format, on v2 (``legacy`` False, by default derived from the path): there a
    malformed header is an error. On v1 a header of another format counts as absent, so the
    ``api_key`` parameter is looked at. The first ``api_key`` query parameter counts only if
    it is of our format: another one (an api.nasa.gov key, ``DEMO_KEY``) is ignored, so
    (None, "query").
    """
    if legacy is None:
        legacy = is_legacy_path(scope.get("path", ""))
    header = _header_value(scope)
    if header and (not legacy or is_our_key_format(header)):
        return header, "header"
    query = _query_value(scope)
    if query is None:
        return None, ""
    return (query if is_our_key_format(query) else None), "query"


def _header_value(scope: Scope) -> str:
    wanted = API_KEY_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers", ()):
        if name == wanted:
            return str(value.decode("latin-1").strip())
    return ""


def _query_value(scope: Scope) -> str | None:
    raw = scope.get("query_string", b"")
    if not raw:
        return None
    for name, value in parse_qsl(raw.decode("latin-1"), keep_blank_values=True):
        if name == API_KEY_PARAM:
            return value
    return None


def client_key(scope: Scope) -> str:
    """The client address; an IPv6 one is reduced to its /64 network (one subscriber owns a
    whole /64, so per-address keys would make the limit free to evade)."""
    client = scope.get("client")
    if not client:
        return UNKNOWN_CLIENT
    address = client[0]
    try:
        parsed = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return address
    if parsed.version == 6:
        # IPv4-mapped addresses are IPv4 clients.
        mapped = parsed.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return address


def rate_identity(scope: Scope) -> RateIdentity:
    """The identity chosen by the rate-limit middleware; the client address without one."""
    identity = scope.get(IDENTITY_SCOPE_KEY)
    if isinstance(identity, RateIdentity):
        return identity
    return RateIdentity(kind="ip", key=client_key(scope))


@dataclass(frozen=True)
class ResolvedKey:
    status: KeyStatus
    account_id: int | None = None
    key_id: int | None = None


class ApiKeyResolver:
    """Key → account lookup with caches and one lookup in flight per key.

    Found keys (also revoked and blocked ones) live in an LRU of ``max_entries``; keys that do
    not exist live in a separate small LRU (``max_negative``), so a flood of made-up keys
    cannot push valid ones out. Entries live ``ttl`` seconds. Concurrent requests for the same
    uncached key share one ``SELECT`` (single flight). Revoking a key or blocking or deleting
    an account through the application drops the affected entries at once (``invalidate_*``)
    and also discards the result of any lookup that was in flight meanwhile; a change made
    behind the application's back is seen within ``ttl``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ttl: float,
        *,
        max_entries: int = DEFAULT_CACHE_SIZE,
        max_negative: int = DEFAULT_NEGATIVE_CACHE_SIZE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self.ttl = ttl
        self._max_entries = max_entries
        self._max_negative = max_negative
        # Public: tests replace it with a fake clock.
        self.clock = clock
        self._cache: OrderedDict[bytes, tuple[float, ResolvedKey]] = OrderedDict()
        self._negative: OrderedDict[bytes, float] = OrderedDict()
        self._pending: dict[bytes, asyncio.Task[ResolvedKey]] = {}
        # Bumped by every invalidation: a lookup started before it must not be cached.
        self._generation = 0

    def peek(self, key: str) -> ResolvedKey | None:
        """The fresh cached answer for a found key (valid, revoked or blocked), else None.
        Never touches the database and ignores the negative cache."""
        cached = self._cache.get(hash_api_key(key))
        if cached is not None and cached[0] > self.clock():
            return cached[1]
        return None

    async def resolve(self, key: str) -> ResolvedKey:
        key_hash = hash_api_key(key)
        now = self.clock()
        cached = self._cache.get(key_hash)
        if cached is not None and cached[0] > now:
            self._cache.move_to_end(key_hash)
            return cached[1]
        expires = self._negative.get(key_hash)
        if expires is not None and expires > now:
            self._negative.move_to_end(key_hash)
            return ResolvedKey("invalid")
        task = self._pending.get(key_hash)
        if task is None:
            task = asyncio.ensure_future(self._load(key_hash, self._generation))
            self._pending[key_hash] = task
            task.add_done_callback(functools.partial(self._finished, key_hash))
        # Shielded: a waiter that goes away (client disconnect) must not cancel the lookup
        # the others are waiting for.
        return await asyncio.shield(task)

    def _finished(self, key_hash: bytes, task: "asyncio.Task[ResolvedKey]") -> None:
        if self._pending.get(key_hash) is task:
            del self._pending[key_hash]
        if not task.cancelled():
            task.exception()  # mark retrieved; waiters see it through the shield

    async def _load(self, key_hash: bytes, generation: int) -> ResolvedKey:
        async with self._session_factory() as session:
            found = await keys_repo.find_key_by_hash(session, key_hash)
        if found is None:
            resolved = ResolvedKey("invalid")
        else:
            status: KeyStatus = "revoked" if found.revoked else "blocked" if found.blocked else "ok"
            resolved = ResolvedKey(status, found.account_id, found.key_id)
        if generation == self._generation:
            self._store(key_hash, resolved)
        return resolved

    def _store(self, key_hash: bytes, resolved: ResolvedKey) -> None:
        if self.ttl <= 0:
            return
        expires = self.clock() + self.ttl
        if resolved.status == "invalid":
            self._negative[key_hash] = expires
            self._negative.move_to_end(key_hash)
            while len(self._negative) > self._max_negative:
                self._negative.popitem(last=False)
            return
        self._cache[key_hash] = (expires, resolved)
        self._cache.move_to_end(key_hash)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def invalidate_key(self, key_id: int) -> None:
        self._generation += 1
        self._drop(lambda entry: entry.key_id == key_id)

    def invalidate_account(self, account_id: int) -> None:
        self._generation += 1
        self._drop(lambda entry: entry.account_id == account_id)

    def clear(self) -> None:
        self._generation += 1
        self._cache.clear()
        self._negative.clear()

    def __len__(self) -> int:
        return len(self._cache) + len(self._negative)

    def _drop(self, matches: Callable[[ResolvedKey], bool]) -> None:
        for key_hash in [h for h, (_, entry) in self._cache.items() if matches(entry)]:
            del self._cache[key_hash]
