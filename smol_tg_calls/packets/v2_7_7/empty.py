from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage


class EmptyMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 254
    REQUIRES_ACK = False

    __slots__ = ()

    def __init__(self) -> None:
        ...

    @classmethod
    def read(cls, data: BytesIO) -> EmptyMessage:
        return EmptyMessage()

    def write(self) -> bytes:
        return b""
