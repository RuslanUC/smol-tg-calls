from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.base import PacketPayloadBase
from smol_tg_calls.utils import uint_le_from_bytes, u8le_to_bytes, u32le_to_bytes, u16le_to_bytes


class PacketStreamData(PacketPayloadBase):
    PACKET_TYPE = 4

    __slots__ = ("stream_id", "pts", "extra_fec", "keyframe", "fragment_index", "fragment_count", "data",)

    def __init__(
            self, stream_id: int, pts: int, extra_fec: bool, keyframe: bool,
            fragment_index: int | None, fragment_count: int | None, data: bytes,
    ) -> None:
        self.stream_id = stream_id
        self.pts = pts
        self.extra_fec = extra_fec
        self.keyframe = keyframe
        self.fragment_index = fragment_index
        self.fragment_count = fragment_count
        self.data = data

    @classmethod
    def read(cls, data: BytesIO) -> PacketStreamData:
        stream_id = uint_le_from_bytes(data.read(1))
        flags = stream_id & 0xc0
        stream_id &= 0x3f
        if flags & 0x40:
            sdlen = uint_le_from_bytes(data.read(2))
        else:
            sdlen = uint_le_from_bytes(data.read(1))
        pts = uint_le_from_bytes(data.read(4))
        fragmented = bool(sdlen & (1 << 14))
        extra_fec = bool(sdlen & (1 << 13))
        keyframe = bool(sdlen & (1 << 15))
        fragment_index = fragment_count = None
        if fragmented:
            fragment_index = uint_le_from_bytes(data.read(1))
            fragment_count = uint_le_from_bytes(data.read(1))
        sdlen &= 0x0f77
        media = data.read(sdlen)
        return cls(
            stream_id=stream_id,
            pts=pts,
            extra_fec=extra_fec,
            keyframe=keyframe,
            fragment_index=fragment_index,
            fragment_count=fragment_count,
            data=media,
        )

    def write(self) -> bytes:
        sdlen = len(self.data)
        fragment_idx_cnt = b""
        if self.fragment_index and self.fragment_count:
            fragment_idx_cnt = (
                    u8le_to_bytes(self.fragment_index)
                    + u8le_to_bytes(self.fragment_count)
            )
            sdlen |= 1 << 14
        if self.extra_fec:
            sdlen |= 1 << 13
        if self.keyframe:
            sdlen |= 1 << 15
        stream_id = self.stream_id
        if sdlen > 255:
            stream_id |= 0x40

        return b"".join([
            u8le_to_bytes(stream_id),
            u16le_to_bytes(sdlen) if sdlen > 255 else u8le_to_bytes(sdlen),
            u32le_to_bytes(self.pts),
            fragment_idx_cnt,
            self.data,
        ])
