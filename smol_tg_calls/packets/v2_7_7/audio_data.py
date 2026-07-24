from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.packets.v2_7_7.bytes_value import BytesValue


class AudioDataMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 5
    REQUIRES_ACK = False

    __slots__ = ("data",)

    def __init__(self, data: bytes) -> None:
        self.data = data

    @classmethod
    def read(cls, data: BytesIO) -> AudioDataMessage:
        data = BytesValue.read(data).value
        return AudioDataMessage(
            data=data,
        )

    def write(self) -> bytes:
        return BytesValue(self.data).write()
