"""Gate for heavy searches: a server-wide limit and a limit per client address."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.errors import ServiceBusy

# Heavy searches of one client address (the key of the rate limiter) at the same time.
PER_CLIENT_LIMIT = 1


class HeavyQueryGate:
    """Counting gate without a queue: a full gate refuses at once with ``ServiceBusy``.

    At most ``limit`` heavy searches run at the same time on the whole server and at most
    ``per_client`` per client key. The application is one process on one event loop and taking
    a place never awaits, so plain counters are exact (a semaphore that never waits). The place
    is released in ``finally``, also when the request is cancelled (client disconnect) or
    fails; a client's entry is removed when its count reaches zero, so the dict cannot grow.
    """

    def __init__(self, limit: int, per_client: int = PER_CLIENT_LIMIT) -> None:
        if limit < 1 or per_client < 1:
            raise ValueError("limit and per_client must be >= 1")
        self.limit = limit
        self.per_client = per_client
        self.in_use = 0
        self.by_client: dict[str, int] = {}

    @asynccontextmanager
    async def slot(self, client: str | None = None) -> AsyncIterator[None]:
        """Take a place for ``client`` (None: no per-client limit, e.g. internal callers)."""
        if self.in_use >= self.limit or (
            client is not None and self.by_client.get(client, 0) >= self.per_client
        ):
            raise ServiceBusy(self.in_use)
        self.in_use += 1
        if client is not None:
            self.by_client[client] = self.by_client.get(client, 0) + 1
        try:
            yield
        finally:
            self.in_use -= 1
            if client is not None:
                left = self.by_client[client] - 1
                if left > 0:
                    self.by_client[client] = left
                else:
                    del self.by_client[client]
