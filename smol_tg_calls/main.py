from __future__ import annotations

import asyncio
import os

from pyrogram import Client

from smol_tg_calls.call import PhoneCall, PhoneCallState
from smol_tg_calls.client import PhoneCallClient


async def main() -> None:
    async with Client(
            "test",
            api_id=int(os.environ["API_ID"]),
            api_hash=os.environ["API_HASH"],
    ) as client:
        # call_client = PhoneCallClient(client, "2.4.4")
        # call_client = PhoneCallClient(client, "2.7.7")
        call_client = PhoneCallClient(client, "5.0.0")
        call_states: dict[int, PhoneCallState] = {}

        @call_client.on_new_call
        async def new_call_handler(call: PhoneCall) -> None:
            print(f"New phone call {call.id}: None -> {call.state}")
            call_states[call.id] = call.state
            if call.state == PhoneCallState.IN_REQUESTED:
                await call.accept()

        @call_client.on_call_update
        async def call_update_handler(call: PhoneCall) -> None:
            print(f"Phone call {call.id}: {call_states[call.id]} -> {call.state}")
            if call.state is PhoneCallState.DISCARDED:
                del call_states[call.id]
            else:
                call_states[call.id] = call.state

            if call.state in (PhoneCallState.IN_ACTIVE, PhoneCallState.OUT_ACTIVE):
                track = call.get_audio_track()
                while (frame := await track.read()) is not None:
                    print(frame)
                track.stop()

        await call_client.start_call(os.environ["CALL_PEER"])
        await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.new_event_loop().run_until_complete(main())