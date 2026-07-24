from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from av.frame import Frame
from pyrogram import Client
from pyrogram.raw.types import UpdatePhoneCallSignalingData, PhoneConnection, PhoneConnectionWebrtc

from smol_tg_calls.crypto import EncryptionX
if TYPE_CHECKING:
    from smol_tg_calls.main import PhoneCall

_TRANSPORT_X_TABLE = (
    (EncryptionX.OUT_TRANSPORT, EncryptionX.IN_TRANSPORT),
    (EncryptionX.IN_TRANSPORT, EncryptionX.OUT_TRANSPORT),
)
_SIGNALING_X_TABLE = (
    (EncryptionX.OUT_SIGNALING, EncryptionX.IN_SIGNALING),
    (EncryptionX.IN_SIGNALING, EncryptionX.OUT_SIGNALING),
)


class PhoneCallProtocol(ABC):
    __slots__ = ("client", "call", "key", "outgoing",)

    def __init__(self, client: Client, call: PhoneCall, key: bytes, outgoing: bool) -> None:
        self.client = client
        self.call = call
        self.key = key
        self.outgoing = outgoing

    @abstractmethod
    async def start(self, connections: list[PhoneConnection | PhoneConnectionWebrtc]) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...

    @abstractmethod
    async def handle_signaling_update(self, update: UpdatePhoneCallSignalingData) -> None:
        ...

    @abstractmethod
    async def recv_audio(self) -> Frame:
        ...

    def transport_x(self, recv: bool) -> int:
        return _TRANSPORT_X_TABLE[self.outgoing][recv]

    def signaling_x(self, recv: bool) -> int:
        return _SIGNALING_X_TABLE[self.outgoing][recv]