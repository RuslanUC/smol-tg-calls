from __future__ import annotations

import asyncio
from typing import TypeVar, Generic

from av.frame import Frame

T = TypeVar("T")


class PhoneCallIncomingTrack:
    def __init__(self) -> None:
        self._av_readers: set[PhoneCallTrackReader[Frame]] = set()

    async def on_new_av_packet(self, data: Frame) -> None:
        for reader in self._av_readers:
            reader.on_new_data(data)

    async def stop(self) -> None:
        ...

    def new_reader(self) -> PhoneCallTrackReader:
        reader = PhoneCallTrackReader(self, False)
        self._av_readers.add(reader)
        return reader

    def stop_reader(self, reader: PhoneCallTrackReader) -> None:
        self._av_readers.remove(reader)

    def has_readers(self) -> bool:
        return len(self._av_readers) > 0


class PhoneCallTrackReader(Generic[T]):
    def __init__(self, track: PhoneCallIncomingTrack, raw: bool) -> None:
        self._queue: asyncio.Queue[T | None] = asyncio.Queue()
        self._track = track
        self._raw = raw
        self._stopped = False

    @property
    def is_raw(self) -> bool:
        return self._raw

    def on_new_data(self, data: T) -> None:
        if self._stopped:
            return
        self._queue.put_nowait(data)

    async def read(self) -> T | None:
        if self._queue.empty() and self._stopped:
            return None
        return await self._queue.get()

    def stop(self) -> None:
        self._stopped = True
        self._queue.put_nowait(None)
        self._track.stop_reader(self)