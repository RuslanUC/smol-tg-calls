from __future__ import annotations

from io import BytesIO

from .base import PacketBase
from ..utils import uint_le_from_bytes, u8le_to_bytes, u16le_to_bytes, u32le_to_bytes


class PacketHeader(PacketBase):
    __slots__ = ("length", "packet_type", "remote_seq", "seq", "acks", "flags",)

    def __init__(
            self, *, packet_type: int, remote_seq: int, seq: int, acks: int, length: int = 0, flags: int = 0,
    ) -> None:
        self.length = length
        self.packet_type = packet_type
        self.remote_seq = remote_seq
        self.seq = seq
        self.acks = acks
        self.flags = flags

    @classmethod
    def read(cls, data: BytesIO) -> PacketHeader:
        return PacketHeader(
            length=uint_le_from_bytes(data.read(2)),
            packet_type=uint_le_from_bytes(data.read(1)),
            remote_seq=uint_le_from_bytes(data.read(4)),
            seq=uint_le_from_bytes(data.read(4)),
            acks=uint_le_from_bytes(data.read(4)),
            flags=uint_le_from_bytes(data.read(1)),
        )

    def write(self) -> bytes:
        return b"".join([
            u16le_to_bytes(self.length),
            u8le_to_bytes(self.packet_type),
            u32le_to_bytes(self.remote_seq),
            u32le_to_bytes(self.seq),
            u32le_to_bytes(self.acks),
            u8le_to_bytes(self.flags),
        ])
