from __future__ import annotations

import asyncio
import os

from pyrogram import Client

from .v2_4_4 import _call_outgoing_v2_4_4


async def main() -> None:
    async with Client(
            "test",
            api_id=int(os.environ["API_ID"]),
            api_hash=os.environ["API_HASH"],
    ) as client:
        await _call_outgoing_v2_4_4(client)
        await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.new_event_loop().run_until_complete(main())