from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.utils import uint_be_from_bytes, u8be_to_bytes


class RemoteBatteryLevelIsLowMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 9
    REQUIRES_ACK = True

    __slots__ = ("battery_low",)

    def __init__(self, battery_low: bool) -> None:
        self.battery_low = battery_low

    @classmethod
    def read(cls, data: BytesIO) -> RemoteBatteryLevelIsLowMessage:
        battery_low = uint_be_from_bytes(data.read(1)) != 0
        return RemoteBatteryLevelIsLowMessage(
            battery_low=battery_low,
        )

    def write(self) -> bytes:
        return u8be_to_bytes(int(self.battery_low))
