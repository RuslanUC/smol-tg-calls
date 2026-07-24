from __future__ import annotations

from enum import Enum, auto

from av.frame import Frame
from pyrogram.raw.types import PhoneCallProtocol as TLPhoneCallProtocol, InputPhoneCall
from pyrogram.types import User

from smol_tg_calls.protocols.base import PhoneCallProtocol
from smol_tg_calls.utils import DhValues


class PhoneCallState(Enum):
    IN_REQUESTED = auto()
    IN_ACCEPTED = auto()
    IN_ACTIVE = auto()
    OUT_REQUESTED = auto()
    OUT_ACTIVE = auto()
    DISCARDED = auto()


class PhoneCall:
    def __init__(
            self,
            call_id: int,
            access_hash: int,
            admin: User,
            participant: User,
            started_at: int,
            protocol: TLPhoneCallProtocol,
            state: PhoneCallState,
    ) -> None:
        self.id = call_id
        self.access_hash = access_hash
        self.admin = admin
        self.participant = participant
        self.started_at = started_at
        self.protocol = protocol
        self.state = state
        self._dh: DhValues | None = None
        self._protocol: PhoneCallProtocol | None = None

    def make_input_call(self) -> InputPhoneCall:
        return InputPhoneCall(
            id=self.id,
            access_hash=self.access_hash,
        )

    async def recv_audio(self) -> Frame:
        return self._protocol.recv_audio()
