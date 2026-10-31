import asyncio
import uuid
from typing import Any

QUEUE_MAXSIZE = 100


class Hub:
    """In-process fan-out keyed by organization (tenant scope).

    Subscribers register a queue; publish() enqueues to every in-scope queue
    and never raises into the caller, so a slow/failed consumer cannot corrupt
    the ingesting request (delivery failure does not corrupt state).
    """

    def __init__(self) -> None:
        self._orgs: dict[uuid.UUID, set[asyncio.Queue]] = {}

    def subscribe(self, organization_id: uuid.UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._orgs.setdefault(organization_id, set()).add(q)
        return q

    def unsubscribe(self, organization_id: uuid.UUID, q: asyncio.Queue) -> None:
        conns = self._orgs.get(organization_id)
        if conns is None:
            return
        conns.discard(q)
        if not conns:
            self._orgs.pop(organization_id, None)

    def publish(self, organization_id: uuid.UUID, message: dict[str, Any]) -> None:
        for q in list(self._orgs.get(organization_id, ())):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # drop slow consumer; never propagate into the caller
                continue

    def clear(self) -> None:
        self._orgs.clear()


hub = Hub()
