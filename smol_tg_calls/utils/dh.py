import os
from hashlib import sha256
from typing import NamedTuple

from pyrogram import Client
from pyrogram.raw.functions.messages import GetDhConfig


class DhStuff(NamedTuple):
    prime: int
    gen: int
    x: int
    g_x: bytes
    g_x_hash: bytes


async def do_all_dh_stuff(client: Client) -> DhStuff:
    df_config = await client.invoke(GetDhConfig(version=0, random_length=0))
    prime = int.from_bytes(df_config.p, "big", signed=False)
    gen: int = df_config.g

    while (a := int.from_bytes(os.urandom(256), "big", signed=False)) > prime:
        print("generated a > prime lol")

    g_a = pow(gen, a, prime)
    g_a_bytes = g_a.to_bytes(256, "big", signed=False)
    g_a_hash = sha256(g_a_bytes).digest()

    return DhStuff(prime, gen, a, g_a_bytes, g_a_hash)