"""Server-sent events: a thread-safe in-process bus and the /lab/api/events stream."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

PING_S = 15.0
_CLOSE = object()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def sse(event: str, data: Any) -> str:
    body = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return f'event: {event}\n' + ''.join(f'data: {line}\n' for line in body.split('\n')) + '\n'


class Subscription:
    def __init__(self, bus: 'EventBus', loop: asyncio.AbstractEventLoop, maxsize: int):
        self.bus, self.loop = bus, loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize)

    def _put(self, item):
        if self.queue.full() and item is not _CLOSE:
            self.queue.get_nowait()  # a slow client loses the oldest event, never blocks the publisher
        self.queue.put_nowait(item)

    def close(self):
        self.bus._remove(self)


class EventBus:
    def __init__(self):
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()
        self.closed = False

    def subscribe(self, maxsize: int = 1024) -> Subscription:
        sub = Subscription(self, asyncio.get_running_loop(), maxsize)
        with self._lock:
            self._subs.add(sub)
        return sub

    def _remove(self, sub: Subscription):
        with self._lock:
            self._subs.discard(sub)

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def _deliver(self, item):
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub._put, item)
            except RuntimeError:  # event loop already closed
                self._remove(sub)

    def publish(self, event: str, data: Any):
        if not self.closed:
            self._deliver((event, json.dumps(data, ensure_ascii=False, separators=(',', ':'))))

    def close(self):
        """Ends every open stream (used on gateway shutdown so uvicorn can drain connections)."""
        self.closed = True
        self._deliver(_CLOSE)


class LogFollower:
    """Follows one log file from its current end; tolerates truncation and late creation."""

    def __init__(self, name: str, path: Path):
        self.name, self.path = name, path
        self.offset = path.stat().st_size if path.exists() else 0
        self._rest = b''

    def poll(self, max_bytes: int = 256 * 1024) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset, self._rest = 0, b''
        if size == self.offset:
            return []
        with self.path.open('rb') as f:
            f.seek(max(self.offset, size - max_bytes))
            chunk = f.read(size - self.offset)
        self.offset = size
        data = self._rest + chunk
        *lines, self._rest = data.split(b'\n')
        return [line.decode('utf-8', 'replace') for line in lines]


async def stream(bus: EventBus, initial: list[tuple[str, Any]],
                 followers: list[LogFollower] = ()) -> AsyncIterator[str]:
    if bus.closed:
        return
    sub = bus.subscribe()
    try:
        yield 'retry: 3000\n\n'
        for event, data in initial:
            yield sse(event, data)
        next_ping = time.monotonic() + PING_S
        while True:
            wait = next_ping - time.monotonic()
            if followers:
                wait = min(wait, 1.0)
            item = None
            try:
                item = await asyncio.wait_for(sub.queue.get(), max(wait, 0))
            except TimeoutError:
                pass
            if item is _CLOSE:
                return
            if item is not None:
                yield sse(*item)
            for f in followers:
                lines = f.poll()
                if lines:
                    yield sse('log', {'log': f.name, 'lines': lines})
            if time.monotonic() >= next_ping:
                yield sse('ping', {'t': utc_now(), 'subscribers': bus.subscribers})
                next_ping = time.monotonic() + PING_S
    finally:
        sub.close()
