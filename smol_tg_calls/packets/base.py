from abc import ABC, abstractmethod
from io import BytesIO
from typing import Self

from smol_tg_calls.utils import SlotsRepr


class PacketBase(ABC, SlotsRepr):
    __slots__ = ()

    @classmethod
    @abstractmethod
    def read(cls, data: BytesIO) -> Self:
        ...

    @abstractmethod
    def write(self) -> bytes:
        ...


class PacketPayloadBase(PacketBase, ABC):
    PACKET_TYPE: int

    __slots__ = ()
