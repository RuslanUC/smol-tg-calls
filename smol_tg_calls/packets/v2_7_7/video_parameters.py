from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.utils import uint_be_from_bytes, u32be_to_bytes


class VideoParametersMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 8
    REQUIRES_ACK = True

    __slots__ = ("aspect_ratio",)

    def __init__(self, aspect_ratio: int) -> None:
        self.aspect_ratio = aspect_ratio

    @classmethod
    def read(cls, data: BytesIO) -> VideoParametersMessage:
        aspect_ratio = uint_be_from_bytes(data.read(4))
        return VideoParametersMessage(
            aspect_ratio=aspect_ratio,
        )

    def write(self) -> bytes:
        return u32be_to_bytes(self.aspect_ratio)
