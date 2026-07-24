from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.utils import uint_be_from_bytes, u8be_to_bytes


class RemoteNetworkStatusMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 10
    REQUIRES_ACK = True

    __slots__ = ("is_low_cost", "is_low_data_requested",)

    def __init__(self, is_low_cost: bool, is_low_data_requested: bool) -> None:
        self.is_low_cost = is_low_cost
        self.is_low_data_requested = is_low_data_requested

    @classmethod
    def read(cls, data: BytesIO) -> RemoteNetworkStatusMessage:
        is_low_cost = uint_be_from_bytes(data.read(1)) != 0
        is_low_data_requested = uint_be_from_bytes(data.read(1)) != 0
        return RemoteNetworkStatusMessage(
            is_low_cost=is_low_cost,
            is_low_data_requested=is_low_data_requested,
        )

    def write(self) -> bytes:
        return b"".join([
            u8be_to_bytes(int(self.is_low_cost)),
            u8be_to_bytes(int(self.is_low_data_requested)),
        ])
