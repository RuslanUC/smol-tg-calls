from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.utils import uint_be_from_bytes, u32be_to_bytes


class AckMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 255
    REQUIRES_ACK = False

    __slots__ = ("seq",)

    def __init__(self, seq: int) -> None:
        self.seq = seq

    @classmethod
    def read(cls, data: BytesIO) -> AckMessage:
        seq = uint_be_from_bytes(data.read(4))
        return AckMessage(
            seq=seq,
        )

    def write(self) -> bytes:
        return u32be_to_bytes(self.seq)
