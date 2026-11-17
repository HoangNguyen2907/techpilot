import asyncio
from collections.abc import Awaitable, Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

MAX_RETRIES = 3


def _is_transient(error: Exception) -> bool:
    return isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)) or (
        isinstance(error, APIStatusError) and error.status_code >= 500
    )


async def create_with_retries[T](operation: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await operation()
        except Exception as error:
            if attempt == MAX_RETRIES or not _is_transient(error):
                raise
            retry_after = getattr(error, "response", None)
            retry_after = (
                retry_after.headers.get("retry-after") if retry_after else None
            )
            try:
                delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
            except ValueError:
                delay = 0.5 * (2**attempt)
            await asyncio.sleep(min(delay, 30.0))
    raise AssertionError("unreachable")
