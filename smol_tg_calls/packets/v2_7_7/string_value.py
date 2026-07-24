from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets import PacketBase
from smol_tg_calls.utils import uint_be_from_bytes, u32be_to_bytes


class StringValue(PacketBase):
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    @classmethod
    def read(cls, data: BytesIO) -> StringValue:
        length = uint_be_from_bytes(data.read(4))
        string_bytes = data.read(length)
        return StringValue(string_bytes.decode("utf8"))

    def write(self) -> bytes:
        string_bytes = self.value.encode("utf8")
        return u32be_to_bytes(len(string_bytes)) + string_bytes
