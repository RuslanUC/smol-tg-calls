from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.base import PacketPayloadBase
from smol_tg_calls.utils import u32le_to_bytes, uint_le_from_bytes


class PacketPong(PacketPayloadBase):
    PACKET_TYPE = 7

    __slots__ = ("out_seq",)

    def __init__(self, out_seq: int) -> None:
        self.out_seq = out_seq

    @classmethod
    def read(cls, data: BytesIO) -> PacketPong:
        out_seq = uint_le_from_bytes(data.read(4))
        return cls(out_seq=out_seq)

    def write(self) -> bytes:
        return u32le_to_bytes(self.out_seq)
