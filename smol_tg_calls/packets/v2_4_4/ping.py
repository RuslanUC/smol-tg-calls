from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.base import PacketPayloadBase


class PacketPing(PacketPayloadBase):
    PACKET_TYPE = 6

    __slots__ = ()

    def __init__(self) -> None:
        ...

    @classmethod
    def read(cls, data: BytesIO) -> PacketPing:
        return cls()

    def write(self) -> bytes:
        return b""
