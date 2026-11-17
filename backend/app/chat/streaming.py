import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

_END = object()


class ClosingStreamingResponse(StreamingResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                await close()


def format_sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def with_heartbeats(
    source: AsyncGenerator[str, None], interval_seconds: float = 15
) -> AsyncGenerator[str, None]:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=1)

    async def pump() -> None:
        async for event in source:
            await queue.put(event)
        await queue.put(_END)

    producer = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval_seconds)
            except TimeoutError:
                if producer.done():
                    await producer
                    return
                yield ": keep-alive\n\n"
                continue

            if item is _END:
                return
            yield str(item)
    finally:
        if not producer.done():
            producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
        await source.aclose()
