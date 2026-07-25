from __future__ import annotations

import os
from typing import Literal, Any, Callable, Awaitable

from pyrogram import Client
from pyrogram.handlers import RawUpdateHandler
from pyrogram.raw.functions.phone import RequestCall
from pyrogram.raw.types import Channel, UpdatePhoneCallSignalingData, UpdatePhoneCall, PhoneCallRequested, \
    PhoneCallProtocol as TLPhoneCallProtocol, PhoneCallAccepted, PhoneCallDiscarded, PhoneCallEmpty, \
    PhoneCallWaiting, PhoneCall as TLPhoneCall
from pyrogram.raw.types.phone import PhoneCall as TLPhonePhoneCall
from pyrogram.types import User, Chat

from smol_tg_calls.call import PhoneCall, PhoneCallState
from smol_tg_calls.utils.dh import prepare_dh

ProtocolVersion = Literal["2.4.4", "2.7.7"]
PhoneCallTypes = TLPhoneCall | PhoneCallAccepted | PhoneCallDiscarded | PhoneCallEmpty | PhoneCallRequested \
                 | PhoneCallWaiting
CallCallback = Callable[[PhoneCall], Awaitable[Any]]


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
        self._on_new_call_handlers: set[CallCallback] = set()
        self._on_call_update_handlers: set[CallCallback] = set()

        client.add_handler(RawUpdateHandler(self._raw_updates_handler))

    def on_call_update(self, func: CallCallback) -> CallCallback:
        self._on_call_update_handlers.add(func)
        return func

    def on_new_call(self, func: CallCallback) -> CallCallback:
        self._on_new_call_handlers.add(func)
        return func

    async def discard_call(self, call_id: int) -> None:
        if call_id not in self._phone_calls:
            return
        await self._phone_calls[call_id].discard()

    async def notify_new_call(self, call: PhoneCall) -> None:
        for handler in self._on_new_call_handlers:
            try:
                await handler(call)
            except Exception as e:
                print(f"Handler exception: {e.__class__.__name__}: {e}")

    async def notify_call_updated(self, call_id: int) -> None:
        if call_id not in self._phone_calls:
            return
        call = self._phone_calls[call_id]
        for handler in self._on_call_update_handlers:
            try:
                await handler(call)
            except Exception as e:
                print(f"Handler exception: {e.__class__.__name__}: {e}")

    async def start_call(self, user_id: str | int) -> PhoneCall:
        dh = await prepare_dh(self.client)
        request_result = await self.client.invoke(RequestCall(
            user_id=await self.client.resolve_peer(user_id),
            random_id=int.from_bytes(os.urandom(4), signed=True),
            g_a_hash=dh.g_x_hash,
            protocol=self.make_protocol(),
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
            _g_a_hash=None,
            _client=self,
        )
        our_call._dh = dh

        await self.notify_new_call(our_call)
        return self._phone_calls[call.id]

    def make_protocol(self) -> TLPhoneCallProtocol:
        return TLPhoneCallProtocol(
            min_layer=65,
            max_layer=92,
            library_versions=self.protocol_versions,
            udp_p2p=False,
            udp_reflector=True,
        )

    async def _handle_call_update(
            self, update: UpdatePhoneCall, users: dict[int, User], chats: dict[int, Chat | Channel]
    ) -> None:
        call = update.phone_call

        if isinstance(call, PhoneCallRequested):
            if not self._on_new_call_handlers:
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
                    _g_a_hash=call.g_a_hash,
                    _client=self,
                )
            await self.notify_new_call(self._phone_calls[call.id])
        elif isinstance(call, PhoneCallDiscarded):
            if call.id in self._phone_calls:
                our_call = self._phone_calls[call.id]
                await our_call.on_call_stopped()
                await self.notify_call_updated(call.id)
        elif isinstance(call, PhoneCallAccepted):
            if call.id not in self._phone_calls:
                return
            our_call = self._phone_calls[call.id]
            await our_call.on_outgoing_call_accepted(call)
        elif isinstance(call, TLPhoneCall):
            if call.id not in self._phone_calls:
                return
            our_call = self._phone_calls[call.id]
            await our_call.on_incoming_call_confirmed(call)

    async def _handle_signaling_update(self, update: UpdatePhoneCallSignalingData) -> None:
        if update.phone_call_id not in self._phone_calls:
            return
        await self._phone_calls[update.phone_call_id].on_signaling_update(update)

    async def _raw_updates_handler(
            self, _: Client, update: Any, users: dict[int, User], chats: dict[int, Chat | Channel],
    ) -> None:
        if isinstance(update, UpdatePhoneCall):
            await self._handle_call_update(update, users, chats)
        elif isinstance(update, UpdatePhoneCallSignalingData):
            await self._handle_signaling_update(update)