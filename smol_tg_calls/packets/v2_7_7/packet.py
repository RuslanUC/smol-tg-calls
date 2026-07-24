from __future__ import annotations

import os
from io import BytesIO

from smol_tg_calls.packets import PacketBase
from smol_tg_calls.packets.v2_7_7 import VideoParametersMessage, AckMessage, CandidatesListMessage, EmptyMessage, \
    RemoteBatteryLevelIsLowMessage, RemoteMediaStateMessage, RemoteNetworkStatusMessage, RequestVideoMessage, \
    VideoFormatsMessage
from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.utils import uint_be_from_bytes, uint_le_from_bytes, u32be_to_bytes, u8be_to_bytes


class LegacySignalingPacket(PacketBase):
    __slots__ = ("seq", "needs_ack", "packet_type", "payload",)

    def __init__(
            self, seq: int, payload: LegacySignalingPacketMessage | bytes, needs_ack: bool = False,
            packet_type: int | None = None,
    ) -> None:
        self.seq = seq
        self.needs_ack = needs_ack
        self.packet_type = packet_type
        self.payload = payload

    @classmethod
    def read(cls, data: BytesIO) -> list[LegacySignalingPacket]:
        result = []

        cur = data.tell()
        total_size = data.seek(0, os.SEEK_END)
        data.seek(cur)

        while data.tell() < total_size:
            seq = uint_be_from_bytes(data.read(4))
            # single_message = bool(seq & (1 << 31))
            needs_ack = bool(seq & (1 << 30))
            seq &= 0x3fffffff

            packet_type = uint_le_from_bytes(data.read(1))
            if packet_type == CandidatesListMessage.PACKET_TYPE:
                payload = CandidatesListMessage.read(data)
            elif packet_type == VideoFormatsMessage.PACKET_TYPE:
                payload = VideoFormatsMessage.read(data)
            elif packet_type == RequestVideoMessage.PACKET_TYPE:
                payload = RequestVideoMessage.read(data)
            elif packet_type == RemoteMediaStateMessage.PACKET_TYPE:
                payload = RemoteMediaStateMessage.read(data)
            elif packet_type == VideoParametersMessage.PACKET_TYPE:
                payload = VideoParametersMessage.read(data)
            elif packet_type == RemoteBatteryLevelIsLowMessage.PACKET_TYPE:
                payload = RemoteBatteryLevelIsLowMessage.read(data)
            elif packet_type == RemoteNetworkStatusMessage.PACKET_TYPE:
                payload = RemoteNetworkStatusMessage.read(data)
            elif packet_type == EmptyMessage.PACKET_TYPE:
                payload = EmptyMessage.read(data)
            elif packet_type == AckMessage.PACKET_TYPE:
                payload = AckMessage.read(data)
            else:
                payload = data.read()

            result.append(LegacySignalingPacket(
                seq=seq,
                needs_ack=needs_ack,
                payload=payload,
                packet_type=packet_type,
            ))

        return result

    def write(self) -> bytes:
        if isinstance(self.payload, LegacySignalingPacketMessage):
            self.packet_type = self.payload.PACKET_TYPE
            self.needs_ack = self.payload.REQUIRES_ACK
            payload = self.payload.write()
        else:
            payload = self.payload

        seq = self.seq
        # if self.single_message:
        #     seq |= 1 << 31
        if self.needs_ack:
            seq |= 1 << 30

        assert self.packet_type is not None

        return b"".join([
            u32be_to_bytes(seq),
            u8be_to_bytes(self.packet_type),
            payload,
        ])
