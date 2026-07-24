from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets import PacketBase
from smol_tg_calls.utils import uint_be_from_bytes, u16be_to_bytes


class BytesValue(PacketBase):
    __slots__ = ("value",)

    def __init__(self, value: bytes) -> None:
        self.value = value

    @classmethod
    def read(cls, data: BytesIO) -> BytesValue:
        length = uint_be_from_bytes(data.read(2))
        value = data.read(length)
        return BytesValue(value)

    def write(self) -> bytes:
        return u16be_to_bytes(len(self.value)) + self.value
