from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage


class RequestVideoMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 3
    REQUIRES_ACK = True

    __slots__ = ()

    def __init__(self) -> None:
        ...

    @classmethod
    def read(cls, data: BytesIO) -> RequestVideoMessage:
        return RequestVideoMessage()

    def write(self) -> bytes:
        return b""
