import os
from hashlib import sha256
from typing import NamedTuple

from pyrogram import Client
from pyrogram.raw.functions.messages import GetDhConfig


class DhValues(NamedTuple):
    prime: int
    gen: int
    x: int
    g_x: bytes
    g_x_hash: bytes
    g_y_hash: bytes


async def prepare_dh(client: Client) -> DhValues:
    df_config = await client.invoke(GetDhConfig(version=0, random_length=0))
    prime = int.from_bytes(df_config.p, "big", signed=False)
    gen: int = df_config.g

    while (x := int.from_bytes(os.urandom(256), "big", signed=False)) > prime:
        print("generated a > prime lol")

    g_x = pow(gen, x, prime)
    g_x_bytes = g_x.to_bytes(256, "big", signed=False)
    g_x_hash = sha256(g_x_bytes).digest()

    return DhValues(prime, gen, x, g_x_bytes, g_x_hash, b"")