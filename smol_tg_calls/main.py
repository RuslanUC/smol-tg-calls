from __future__ import annotations

import asyncio
import os

from pyrogram import Client

from smol_tg_calls.client import PhoneCallClient


async def main() -> None:
    async with Client(
            "test",
            api_id=int(os.environ["API_ID"]),
            api_hash=os.environ["API_HASH"],
    ) as client:
        call_client = PhoneCallClient(client, "2.4.4")
        # call_client = PhoneCallClient(client, "2.7.7")
        await call_client.start_call(os.environ["CALL_PEER"])
        await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.new_event_loop().run_until_complete(main())