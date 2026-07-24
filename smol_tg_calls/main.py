from __future__ import annotations

import asyncio
import os
from _sha1 import sha1
from enum import Enum, auto
from time import time
from typing import Literal, Any, Callable, Awaitable, cast
from hashlib import sha256

from pyrogram import Client
from pyrogram.handlers import RawUpdateHandler
from pyrogram.raw.functions.phone import AcceptCall, DiscardCall, RequestCall, ConfirmCall
from pyrogram.raw.types import Channel, UpdatePhoneCallSignalingData, UpdatePhoneCall, PhoneCallRequested, \
    PhoneCallProtocol as TLPhoneCallProtocol, InputPhoneCall, PhoneCallAccepted, PhoneCallDiscarded, PhoneCallEmpty, \
    PhoneCallWaiting, PhoneCall as TLPhoneCall, PhoneCallDiscardReasonHangup
from pyrogram.raw.types.phone import PhoneCall as TLPhonePhoneCall
from pyrogram.types import User, Chat

from smol_tg_calls.protocols.base import PhoneCallProtocol
from smol_tg_calls.protocols.v2_4_4 import PhoneCallProtocolV2_4_4
from smol_tg_calls.protocols.v2_7_7 import PhoneCallProtocolV2_7_7
from smol_tg_calls.utils.dh import DhValues, prepare_dh


ProtocolVersion = Literal["2.4.4", "2.7.7"]
PhoneCallTypes = TLPhoneCall | PhoneCallAccepted | PhoneCallDiscarded | PhoneCallEmpty | PhoneCallRequested \
                 | PhoneCallWaiting


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


IncomingCallCallback = Callable[[PhoneCall], Awaitable[bool]]


class PhoneCallClient:
    def __init__(
            self,
            client: Client,
            protocol_versions: ProtocolVersion | list[ProtocolVersion] | None = None,
    ) -> None:
        self.client = client

        self.protocol_versions: list[ProtocolVersion]
        if protocol_versions is None:
            self.protocol_versions = ["2.4.4", "2.7.7"]
        elif isinstance(protocol_versions, str):
            self.protocol_versions = [protocol_versions]
        else:
            self.protocol_versions = protocol_versions

        self._phone_calls: dict[int, PhoneCall] = {}
        self._check_accept_call: IncomingCallCallback | None = None

        client.add_handler(RawUpdateHandler(self._raw_updates_handler))

    def on_incoming_call(self, func: IncomingCallCallback) -> IncomingCallCallback:
        self._check_accept_call = func
        return func

    async def discard_call(self, call_id: int) -> None:
        if call_id not in self._phone_calls:
            return
        call = self._phone_calls.pop(call_id)
        call.state = PhoneCallState.DISCARDED
        await self.client.invoke(DiscardCall(
            peer=call.make_input_call(),
            duration=int(time() - call.started_at),
            reason=PhoneCallDiscardReasonHangup(),
            connection_id=0,
        ))

    async def start_call(self, user_id: str | int) -> PhoneCall:
        dh = await prepare_dh(self.client)
        request_result = await self.client.invoke(RequestCall(
            user_id=await self.client.resolve_peer(user_id),
            random_id=int.from_bytes(os.urandom(4), signed=True),
            g_a_hash=dh.g_x_hash,
            protocol=self._make_protocol(),
            video=False,
        ))
        if not isinstance(request_result, TLPhonePhoneCall) \
                or not isinstance(request_result.phone_call, PhoneCallWaiting):
            raise ValueError(
                f"Expected RequestCall to return phone.PhoneCall with PhoneCallWaiting, got {request_result}"
            )
        call = request_result.phone_call
        self._phone_calls[call.id] = our_call = PhoneCall(
            call_id=call.id,
            access_hash=call.access_hash,
            # TODO: fill admin and participant
            admin=None,
            participant=None,
            started_at=call.date,
            protocol=call.protocol,
            state=PhoneCallState.OUT_REQUESTED,
        )
        our_call._dh = dh

        return self._phone_calls[call.id]

    def _make_protocol(self) -> TLPhoneCallProtocol:
        return TLPhoneCallProtocol(
            min_layer=65,
            max_layer=92,
            library_versions=self.protocol_versions,
            udp_p2p=False,
            udp_reflector=True,
        )

    async def _handle_phone_call(self, call: PhoneCall, tl: TLPhoneCall, key: bytes) -> None:
        versions = tl.protocol.library_versions
        if len(versions) != 1:
            await self.discard_call(call.id)
            print(f"Expected just one protocol version, got {versions}")
            return

        outgoing = call.state is PhoneCallState.OUT_ACTIVE

        version = versions[0]
        if version == "2.4.4":
            protocol = PhoneCallProtocolV2_4_4(self.client, call, key, outgoing)
        elif version == "2.7.7":
            protocol = PhoneCallProtocolV2_7_7(self.client, call, key, outgoing)
        else:
            await self.discard_call(call.id)
            print(f"Got unsupported protocol: {version}")
            return

        call._protocol = protocol
        await protocol.start(tl.connections)

    async def _handle_call_update(
            self, update: UpdatePhoneCall, users: dict[int, User], chats: dict[int, Chat | Channel]
    ) -> None:
        call = update.phone_call
        print(call)

        if isinstance(call, PhoneCallRequested):
            if self._check_accept_call is None:
                return
            if call.id not in self._phone_calls:
                self._phone_calls[call.id] = PhoneCall(
                    call_id=call.id,
                    access_hash=call.access_hash,
                    admin=users[call.admin_id],
                    participant=users[call.participant_id],
                    started_at=call.date,
                    protocol=call.protocol,
                    state=PhoneCallState.IN_REQUESTED,
                )
            our_call = self._phone_calls[call.id]
            if await self._check_accept_call(our_call):
                our_call._dh = dh = await prepare_dh(self.client)
                dh.g_y_hash = call.g_a_hash
                accept_result = await self.client.invoke(AcceptCall(
                    peer=our_call.make_input_call(),
                    g_b=dh.g_x,
                    protocol=self._make_protocol(),
                ))
                if not isinstance(accept_result, TLPhonePhoneCall) \
                        or not isinstance(accept_result.phone_call, PhoneCallWaiting):
                    await self.discard_call(call.id)
                    print(f"Expected AcceptCall to return phone.PhoneCall with PhoneCallWaiting, got {accept_result}")
                    return
                print(accept_result.phone_call)
                our_call.state = PhoneCallState.IN_ACCEPTED
            else:
                del self._phone_calls[call.id]
        elif isinstance(call, PhoneCallDiscarded):
            if call.id in self._phone_calls:
                del self._phone_calls[call.id]
        elif isinstance(call, PhoneCallAccepted):
            if call.id not in self._phone_calls:
                return
            our_call = self._phone_calls[call.id]
            if our_call.state is not PhoneCallState.OUT_REQUESTED:
                return

            dh = cast(DhValues, our_call._dh)
            g_b = int.from_bytes(call.g_b, "big", signed=False)
            key = pow(g_b, dh.x, dh.prime).to_bytes(256, "big", signed=False)

            result = await self.client.invoke(ConfirmCall(
                peer=our_call.make_input_call(),
                g_a=dh.g_x,
                key_fingerprint=int.from_bytes(sha1(key).digest()[-8:], "little", signed=True),
                protocol=self._make_protocol(),
            ))
            if not isinstance(result, TLPhonePhoneCall) \
                    or not isinstance(result.phone_call, TLPhoneCall):
                await self.discard_call(call.id)
                print(f"Expected AcceptCall to return phone.PhoneCall with PhoneCall, got {result}")
                return

            our_call.state = PhoneCallState.IN_ACTIVE
            await self._handle_phone_call(our_call, result.phone_call, key)
        elif isinstance(call, TLPhoneCall):
            if call.id not in self._phone_calls:
                return
            our_call = self._phone_calls[call.id]
            if our_call.state is not PhoneCallState.IN_ACCEPTED:
                return

            dh = cast(DhValues, our_call._dh)
            if sha256(call.g_a_or_b).digest() != dh.g_y_hash:
                await self.discard_call(call.id)
                print(f"Invalid g_a_or_b: g_a hash mismatch")
                return
            g_a = int.from_bytes(call.g_a_or_b, "big", signed=False)
            key = pow(g_a, dh.x, dh.prime).to_bytes(256, "big", signed=False)
            key_fp = int.from_bytes(sha1(key).digest()[-8:], "little", signed=True)
            if key_fp != call.key_fingerpring:
                await self.discard_call(call.id)
                print(f"Invalid key fingerprint")
                return

            our_call.state = PhoneCallState.IN_ACTIVE
            await self._handle_phone_call(our_call, call, key)

    async def _handle_signaling_update(
            self, update: UpdatePhoneCallSignalingData, users: dict[int, User], chats: dict[int, Chat | Channel]
    ) -> None:
        if update.phone_call_id not in self._phone_calls:
            return
        await self._phone_calls[update.phone_call_id]._protocol.handle_signaling_update(update)

    async def _raw_updates_handler(
            self, _: Client, update: Any, users: dict[int, User], chats: dict[int, Chat | Channel],
    ) -> None:
        if isinstance(update, UpdatePhoneCall):
            await self._handle_call_update(update, users, chats)
        elif isinstance(update, UpdatePhoneCallSignalingData):
            await self._handle_signaling_update(update, users, chats)


async def main() -> None:
    async with Client(
            "test",
            api_id=int(os.environ["API_ID"]),
            api_hash=os.environ["API_HASH"],
    ) as client:
        call_client = PhoneCallClient(client, "2.7.7")
        await call_client.start_call(os.environ["CALL_PEER"])
        await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.new_event_loop().run_until_complete(main())