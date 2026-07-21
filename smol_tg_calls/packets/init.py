from __future__ import annotations

from io import BytesIO

from .base import PacketPayloadBase
from ..utils import uint_le_from_bytes, u32le_to_bytes, u8le_to_bytes


class PacketInit(PacketPayloadBase):
    PACKET_TYPE = 1

    __slots__ = ("version", "min_version", "flags", "audio_streams", "video_streams",)

    def __init__(
            self, version: int, min_version: int, flags: int, audio_streams: list[int], video_streams: list[int],
    ) -> None:
        self.version = version
        self.min_version = min_version
        self.flags = flags
        self.audio_streams = audio_streams
        self.video_streams = video_streams

    @classmethod
    def read(cls, data: BytesIO) -> PacketInit:
        version = uint_le_from_bytes(data.read(4))
        min_version = uint_le_from_bytes(data.read(4))
        flags = uint_le_from_bytes(data.read(4))
        audio_streams_count = uint_le_from_bytes(data.read(1))
        audio_streams = [
            uint_le_from_bytes(data.read(4))
            for _ in range(audio_streams_count)
        ]
        video_streams_count = uint_le_from_bytes(data.read(1))
        video_streams = [
            uint_le_from_bytes(data.read(4))
            for _ in range(video_streams_count)
        ]
        data.read(1)

        return cls(
            version=version,
            min_version=min_version,
            flags=flags,
            audio_streams=audio_streams,
            video_streams=video_streams,
        )

    def write(self) -> bytes:
        return b"".join([
            u32le_to_bytes(self.version),
            u32le_to_bytes(self.min_version),
            u32le_to_bytes(self.flags),
            u8le_to_bytes(len(self.audio_streams)),
            b"".join((
                u32le_to_bytes(stream)
                for stream in self.audio_streams
            )),
            u8le_to_bytes(len(self.video_streams)),
            b"".join((
                u32le_to_bytes(stream)
                for stream in self.video_streams
            )),
            b"\x00",  # TODO: what is this?
        ])
