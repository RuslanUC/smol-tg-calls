from typing import Awaitable, TypeVar

T1 = TypeVar("T1")
T2 = TypeVar("T2")


async def coro_with_additional_return(coro: Awaitable[T1], add: T2) -> tuple[T1, T2]:
    return await coro, add
