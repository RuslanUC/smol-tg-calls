from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.packets.v2_7_7.string_value import StringValue
from smol_tg_calls.utils import uint_be_from_bytes, u8be_to_bytes


class VideoFormatsMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 2
    REQUIRES_ACK = True

    __slots__ = ("formats", "encoders_count",)

    def __init__(self, formats: dict[str, dict[str, str]], encoders_count: int) -> None:
        self.formats = formats
        self.encoders_count = encoders_count

    @classmethod
    def read(cls, data: BytesIO) -> VideoFormatsMessage:
        formats_num = uint_be_from_bytes(data.read(1))
        formats = {}
        for _ in range(formats_num):
            name = StringValue.read(data).value
            params_num = uint_be_from_bytes(data.read(1))
            params = {}
            for _ in range(params_num):
                key = StringValue.read(data).value
                value = StringValue.read(data).value
                params[key] = value
            formats[name] = params

        encoders_count = uint_be_from_bytes(data.read(1))

        return VideoFormatsMessage(
            formats=formats,
            encoders_count=encoders_count,
        )

    def write(self) -> bytes:
        chunks = [u8be_to_bytes(len(self.formats))]

        for name, params in self.formats.items():
            chunks.append(StringValue(name).write())
            chunks.append(u8be_to_bytes(len(params)))
            for key, value in params.items():
                chunks.append(StringValue(key).write())
                chunks.append(StringValue(value).write())

        chunks.append(u8be_to_bytes(self.encoders_count))

        return b"".join(chunks)
