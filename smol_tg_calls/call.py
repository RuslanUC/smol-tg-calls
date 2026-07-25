from __future__ import annotations

from enum import Enum, auto
from time import time
from typing import TYPE_CHECKING, cast

from av.frame import Frame
from pyrogram.raw.functions.phone import AcceptCall, DiscardCall, ConfirmCall
from pyrogram.raw.types import PhoneCallProtocol as TLPhoneCallProtocol, InputPhoneCall, PhoneCallWaiting, \
    PhoneCallDiscardReasonHangup, PhoneCallAccepted, PhoneCall as TLPhoneCall, UpdatePhoneCallSignalingData
from pyrogram.raw.types.phone import PhoneCall as PhonePhoneCall
from pyrogram.types import User

from smol_tg_calls.protocols import PhoneCallProtocolV2_4_4, PhoneCallProtocolV2_7_7
from smol_tg_calls.protocols.base import PhoneCallProtocol
from smol_tg_calls.track import PhoneCallIncomingTrack, PhoneCallTrackReader
from smol_tg_calls.utils import DhValues, prepare_dh
from smol_tg_calls.utils.dh import DhError

if TYPE_CHECKING:
    from smol_tg_calls.client import PhoneCallClient


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
            _g_a_hash: bytes | None,
            _client: PhoneCallClient,
    ) -> None:
        self.id = call_id
        self.access_hash = access_hash
        self.admin = admin
        self.participant = participant
        self.started_at = started_at
        self.protocol = protocol
        self.state = state
        self._g_a_hash = _g_a_hash
        self._client = _client
        self._dh: DhValues | None = None
        self._protocol: PhoneCallProtocol | None = None
        self._audio_track = PhoneCallIncomingTrack()

    def make_input_call(self) -> InputPhoneCall:
        return InputPhoneCall(
            id=self.id,
            access_hash=self.access_hash,
        )

    async def accept(self) -> None:
        self._dh = dh = await prepare_dh(self._client.client)
        self._dh = dh = dh._replace(g_y_hash=cast(bytes, self._g_a_hash))
        accept_result = await self._client.client.invoke(AcceptCall(
            peer=self.make_input_call(),
            g_b=dh.g_x,
            protocol=self._client.make_protocol(),
        ))

        if isinstance(accept_result, PhonePhoneCall) and isinstance(accept_result.phone_call, PhoneCallWaiting):
            self.state = PhoneCallState.IN_ACCEPTED
            return

        await self.discard()
        await self._client.notify_call_updated(self.id)
        raise ValueError(f"Expected AcceptCall to return phone.PhoneCall with PhoneCallWaiting, got {accept_result}")

    async def discard(self) -> None:
        await self.on_call_stopped()
        await self._client.client.invoke(DiscardCall(
            peer=self.make_input_call(),
            duration=int(time() - self.started_at),
            reason=PhoneCallDiscardReasonHangup(),
            connection_id=0,
        ))
        await self._client.notify_call_updated(self.id)

    async def on_call_stopped(self) -> None:
        self.state = PhoneCallState.DISCARDED
        if self._protocol:
            print("stopping protocol")
            await self._protocol.stop()
            self._protocol = None

    async def on_outgoing_call_accepted(self, tl: PhoneCallAccepted) -> None:
        if self.state is not PhoneCallState.OUT_REQUESTED:
            return

        dh = cast(DhValues, self._dh)
        key, key_fp = dh.make_key(tl.g_b, None)

        result = await self._client.client.invoke(ConfirmCall(
            peer=self.make_input_call(),
            g_a=dh.g_x,
            key_fingerprint=key_fp,
            protocol=self._client.make_protocol(),
        ))
        if isinstance(result, PhonePhoneCall) and isinstance(result.phone_call, TLPhoneCall):
            self.state = PhoneCallState.OUT_ACTIVE
            await self._client.notify_call_updated(self.id)
            await self._handle_phone_call(result.phone_call, key)
            return

        await self.discard()
        print(f"Expected AcceptCall to return phone.PhoneCall with PhoneCall, got {result}")

    async def on_incoming_call_confirmed(self, tl: TLPhoneCall) -> None:
        if self.state is not PhoneCallState.IN_ACCEPTED:
            return

        dh = cast(DhValues, self._dh)
        try:
            key, key_fp = dh.make_key(tl.g_a_or_b, tl.key_fingerprint)
        except DhError as e:
            await self.discard()
            print(f"Dh error: {e}")
            return

        self.state = PhoneCallState.IN_ACTIVE
        await self._client.notify_call_updated(self.id)
        await self._handle_phone_call(tl, key)

    async def on_signaling_update(self, update: UpdatePhoneCallSignalingData) -> None:
        if self._protocol is None or self.state not in (PhoneCallState.IN_ACTIVE, PhoneCallState.OUT_ACTIVE):
            return
        await self._protocol.handle_signaling_update(update)

    async def _handle_phone_call(self, call: TLPhoneCall, key: bytes) -> None:
        versions = call.protocol.library_versions
        if len(versions) != 1:
            await self.discard()
            print(f"Expected just one protocol version, got {versions}")
            return

        outgoing = self.state is PhoneCallState.OUT_ACTIVE

        version = versions[0]
        if version == "2.4.4":
            protocol = PhoneCallProtocolV2_4_4(self._client.client, self, key, outgoing)
        elif version == "2.7.7":
            protocol = PhoneCallProtocolV2_7_7(self._client.client, self, key, outgoing)
        else:
            await self.discard()
            print(f"Got unsupported protocol: {version}")
            return

        self._protocol = protocol
        protocol.register_track(self._audio_track)
        await protocol.start(call.connections)

    def get_audio_track(self) -> PhoneCallTrackReader[Frame]:
        return self._audio_track.new_reader()
