"""Async helpers for running blocking integration clients."""
import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def run_sync_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout: float | None = None,
    **kwargs: Any,
) -> T:
    """Run a blocking callable without tying shutdown to asyncio's default executor."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def resolve_result(result: T) -> None:
        if not future.done():
            future.set_result(result)

    def resolve_error(error: BaseException) -> None:
        if not future.done():
            future.set_exception(error)

    def notify(callback: Callable[..., None], value: Any) -> None:
        try:
            loop.call_soon_threadsafe(callback, value)
        except RuntimeError:
            # The caller may have timed out and closed the event loop while the
            # blocking integration call was still unwinding in its daemon thread.
            pass

    def runner() -> None:
        try:
            result = func(*args, **kwargs)
        except BaseException as error:
            notify(resolve_error, error)
        else:
            notify(resolve_result, result)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

    return await asyncio.wait_for(future, timeout=timeout)
