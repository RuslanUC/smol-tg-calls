from __future__ import annotations

import os
from io import BytesIO

from smol_tg_calls.packets.base import PacketBase, PacketPayloadBase
from smol_tg_calls.packets.v2_4_4.header import PacketHeader
from smol_tg_calls.packets.v2_4_4.init import PacketInit
from smol_tg_calls.packets.v2_4_4.init_ack import PacketInitAck
from smol_tg_calls.packets.v2_4_4.ping import PacketPing
from smol_tg_calls.packets.v2_4_4.pong import PacketPong
from smol_tg_calls.packets.v2_4_4.stream_data import PacketStreamData


class Packet(PacketBase):
    __slots__ = ("header", "payload",)

    def __init__(self, header: PacketHeader, payload: PacketPayloadBase | bytes) -> None:
        self.header = header
        self.payload = payload

    @classmethod
    def read(cls, data: BytesIO) -> Packet:
        start = data.tell()
        header = PacketHeader.read(data)
        payload = data.read(header.length - (data.tell() - start) + 2)
        if header.packet_type == 1:
            payload = PacketInit.read(BytesIO(payload))
        elif header.packet_type == 2:
            payload = PacketInitAck.read(BytesIO(payload))
        elif header.packet_type == 4:
            payload = PacketStreamData.read(BytesIO(payload))
        elif header.packet_type == 6:
            payload = PacketPing.read(BytesIO(payload))
        elif header.packet_type == 7:
            payload = PacketPong.read(BytesIO(payload))

        return Packet(header, payload)

    def write(self, pad: bool = False) -> bytes:
        if isinstance(self.payload, PacketPayloadBase):
            self.header.packet_type = self.payload.PACKET_TYPE
            payload_bytes = self.payload.write()
        else:
            payload_bytes = self.payload
        self.header.length = len(self.header.write()) - 2 + len(payload_bytes)
        result = self.header.write() + payload_bytes
        if pad and len(result) % 16 != 0:
            padding = (-len(result)) % 16
            if padding < 16:
                padding += 16
            result += os.urandom(padding)
        return result
