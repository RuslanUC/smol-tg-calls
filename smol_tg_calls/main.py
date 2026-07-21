from __future__ import annotations

import asyncio
import os
import wave
from hashlib import sha256, sha1
from io import BytesIO
from typing import NamedTuple

import opuslib
from pyrogram import Client
from pyrogram.raw.functions.messages import GetDhConfig
from pyrogram.raw.functions.phone import RequestCall, ConfirmCall
from pyrogram.raw.types import InputPhoneCall, UpdatePhoneCall, PhoneCallAccepted, PhoneCallProtocol, PhoneConnection, \
    PhoneCall, UpdatePhoneCallSignalingData
from pyrogram.raw.types.phone import PhoneCall as PhonePhoneCall

from .aioudp import open_remote_endpoint
from .crypto import decrypt, EncryptionX, encrypt
from .packets import Packet, PacketHeader, PacketInit, PacketInitAck, PacketPing, PacketPong, PacketStreamData
from .packets.init_ack import Stream
from .udp_endpoint import UdpEndpoint
from .utils import coro_with_additional_return


class DhStuff(NamedTuple):
    prime: int
    gen: int
    a: int
    g_a: bytes
    g_a_hash: bytes


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


def _make_protocol(version: str | list[str]) -> PhoneCallProtocol:
    return PhoneCallProtocol(
        min_layer=65,
        max_layer=92,
        library_versions=version if isinstance(version, list) else [version],
        udp_p2p=False,
        udp_reflector=True,
    )


SAMPLE_RATE = 48000
CHANNELS = 1
FRAME_MS = 60
FRAME_SIZE = SAMPLE_RATE * FRAME_MS // 1000

opus_frames = []
encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_AUDIO)
with wave.open("audio-mono.wav", "rb") as wav:
    assert wav.getframerate() == SAMPLE_RATE, wav.getframerate()
    assert wav.getnchannels() == CHANNELS, wav.getnchannels()
    assert wav.getsampwidth() == 2, wav.getsampwidth()

    while True:
        pcm = wav.readframes(FRAME_SIZE)

        if len(pcm) < FRAME_SIZE * CHANNELS * 2:
            break

        opus_packet = encoder.encode(pcm, FRAME_SIZE)
        opus_frames.append(opus_packet)


class CallIdk_v2_4_4:
    OPUS = int.from_bytes(b"SUPO", "little", signed=False)

    def __init__(self, dh: DhStuff, call: PhonePhoneCall) -> None:
        self.dh = dh
        self.call = call
        self.key: bytes | None = None
        self.endpoints: list[UdpEndpoint] = []
        self.seq = 0
        self.remote_seq = 0
        self.sending_audio = None

    def _make_input_call(self) -> InputPhoneCall:
        return InputPhoneCall(
            id=self.call.phone_call.id,
            access_hash=self.call.phone_call.access_hash,
        )

    async def handle_call_update(self, client: Client, update: UpdatePhoneCall, _1, _2) -> None:
        if not isinstance(update, UpdatePhoneCall):
            return
        if update.phone_call.id != self.call.phone_call.id:
            return
        print(update)
        if isinstance(update.phone_call, PhoneCallAccepted):
            g_b = int.from_bytes(update.phone_call.g_b, "big", signed=False)
            self.key = key = pow(g_b, self.dh.a, self.dh.prime).to_bytes(256, "big", signed=False)

            client.on_raw_update()(self._handle_signaling_update)

            self.call = await client.invoke(ConfirmCall(
                peer=self._make_input_call(),
                g_a=self.dh.g_a,
                key_fingerprint=int.from_bytes(sha1(key).digest()[-8:], "little", signed=True),
                protocol=_make_protocol("2.4.4"),
            ))

            print(self.call)

            await self._call_udp()

    async def _handle_signaling_update(self, client: Client, update: UpdatePhoneCallSignalingData, _1, _2) -> None:
        if not isinstance(update, UpdatePhoneCallSignalingData):
            return
        assert self.key is not None

        print(f"got signaling data (len={len(update.data)}): {update.data}")
        decrypted, valid = decrypt(update.data, self.key, x=EncryptionX.IN_SIGNALING, ctr=True, ctr_value=0)
        print(f"  decrypted (valid={valid}): {decrypted}")

    async def _req_reflector_peer_self_info(self) -> None:
        await asyncio.gather(*(
            endpoint.get_self_info()
            for endpoint in self.endpoints
        ))

    def _send_to_all(self, data: bytes) -> None:
        for endpoint in self.endpoints:
            endpoint.send(data)

    def _send_init(self) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = Packet(
            header=PacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=PacketInit(
                version=9,
                min_version=9,
                flags=0,
                audio_streams=[self.OPUS],
                video_streams=[],
            )
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_init_ack(self) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = Packet(
            header=PacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=PacketInitAck(
                version=9,
                min_version=9,
                streams=[
                    Stream(
                        stream_id=1,
                        stream_type=1,
                        codec=self.OPUS,
                        frame_duration=60,
                        enabled=1,
                    )
                ],
            )
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_ping(self) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = Packet(
            header=PacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=PacketPing()
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_pong(self) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = Packet(
            header=PacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=PacketPong(self.remote_seq)
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_stream_data(self, data: PacketStreamData) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = Packet(
            header=PacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=data
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_audio_frame(self) -> None:
        assert self.key is not None
        if self.sending_audio >= len(opus_frames):
            return

        self._send_stream_data(PacketStreamData(
            stream_id=1,
            pts=60 * self.sending_audio,
            extra_fec=False,
            keyframe=False,
            fragment_index=None,
            fragment_count=None,
            data=opus_frames[self.sending_audio],
        ))

        self.sending_audio += 1

        asyncio.get_running_loop().call_later(0.060, self._send_audio_frame)

    async def _process_packet(self, data: bytes) -> None:
        assert self.key is not None

        decrypted, valid = decrypt(data, self.key, EncryptionX.IN_TRANSPORT)
        if not valid:
            print(f"Decrypted (valid={valid}) (len={len(decrypted)}): {decrypted}")
            return
        packet = Packet.read(BytesIO(decrypted))
        if packet.header.seq <= self.remote_seq:
            return
        self.remote_seq = packet.header.seq

        print(f"received: {packet!r}")
        payload = packet.payload

        if isinstance(payload, PacketInit):
            self._send_init_ack()
        elif isinstance(payload, PacketInitAck):
            self._send_init()
        elif isinstance(payload, PacketPing):
            self._send_pong()
        elif isinstance(payload, PacketStreamData):
            if self.sending_audio is None:
                self.sending_audio = 0
                asyncio.get_running_loop().call_later(0.060, self._send_audio_frame)
            ...
            # self._send_stream_data(payload)

    async def _call_udp(self) -> None:
        assert self.key is not None

        self.endpoints.clear()

        call: PhoneCall = self.call.phone_call
        if not isinstance(call, PhoneCall):
            raise RuntimeError

        connection: PhoneConnection
        for connection in call.connections:
            if not isinstance(connection, PhoneConnection) or connection.tcp:
                continue
            print(f"Using connection {connection}")
            self.endpoints.append(UdpEndpoint(
                await open_remote_endpoint(connection.ip, connection.port),
                connection.peer_tag,
            ))

        assert self.endpoints

        await self._req_reflector_peer_self_info()

        self.seq = 0
        self.remote_seq = 0
        self._send_init()

        print("Waiting for remote packet...")
        done, pending = await asyncio.wait([
            asyncio.create_task(coro_with_additional_return(endpoint.receive(), endpoint))
            for endpoint in self.endpoints
        ], return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            if not task.done():
                task.cancel()

        data, endpoint = await list(done)[0]
        await self._process_packet(data)

        while True:
            data = await endpoint.receive()
            await self._process_packet(data)



async def _call_outgoing_v2_4_4(client: Client) -> None:
    dh = await do_all_dh_stuff(client)

    call = await client.invoke(RequestCall(
        user_id=await client.resolve_peer(os.environ["CALL_PEER"]),
        random_id=int.from_bytes(os.urandom(4), "big", signed=True),
        g_a_hash=dh.g_a_hash,
        protocol=_make_protocol("2.4.4"),
        video=False,
    ))

    call_idk = CallIdk_v2_4_4(dh, call)
    client.on_raw_update()(call_idk.handle_call_update)


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