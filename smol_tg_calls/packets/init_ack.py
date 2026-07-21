from __future__ import annotations

from io import BytesIO

from .base import PacketPayloadBase, PacketBase
from ..utils import uint_le_from_bytes, u8le_to_bytes, u32le_to_bytes, u16le_to_bytes


class Stream(PacketBase):
    PACKET_TYPE = 256

    __slots__ = ("id", "type", "codec", "frame_duration", "enabled",)

    def __init__(
            self, stream_id: int, stream_type: int, codec: int, frame_duration: int, enabled: int,
    ) -> None:
        self.id = stream_id
        self.type = stream_type
        self.codec = codec
        self.frame_duration = frame_duration
        self.enabled = enabled

    @classmethod
    def read(cls, data: BytesIO) -> Stream:
        stream_id = uint_le_from_bytes(data.read(1))
        stream_type = uint_le_from_bytes(data.read(1))
        codec = uint_le_from_bytes(data.read(4))
        frame_duration = uint_le_from_bytes(data.read(2))
        enabled = uint_le_from_bytes(data.read(1))

        return cls(
            stream_id=stream_id,
            stream_type=stream_type,
            codec=codec,
            frame_duration=frame_duration,
            enabled=enabled,
        )

    def write(self) -> bytes:
        return b"".join([
            u8le_to_bytes(self.id),
            u8le_to_bytes(self.type),
            u32le_to_bytes(self.codec),
            u16le_to_bytes(self.frame_duration),
            u8le_to_bytes(self.enabled),
        ])


class PacketInitAck(PacketPayloadBase):
    PACKET_TYPE = 2

    __slots__ = ("version", "min_version", "streams",)

    def __init__(
            self, version: int, min_version: int, streams: list[Stream],
    ) -> None:
        self.version = version
        self.min_version = min_version
        self.streams = streams

    @classmethod
    def read(cls, data: BytesIO) -> PacketInitAck:
        version = uint_le_from_bytes(data.read(4))
        min_version = uint_le_from_bytes(data.read(4))
        streams_count = uint_le_from_bytes(data.read(1))
        streams = [
            Stream.read(data)
            for _ in range(streams_count)
        ]

        return cls(
            version=version,
            min_version=min_version,
            streams=streams,
        )

    def write(self) -> bytes:
        return b"".join([
            u32le_to_bytes(self.version),
            u32le_to_bytes(self.min_version),
            u8le_to_bytes(len(self.streams)),
            b"".join((
                stream.write()
                for stream in self.streams
            )),
        ])
