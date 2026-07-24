from __future__ import annotations

import asyncio
import os
from hashlib import sha1
from io import BytesIO

from pyrogram import Client
from pyrogram.raw.functions.phone import RequestCall, ConfirmCall
from pyrogram.raw.types import InputPhoneCall, UpdatePhoneCall, PhoneCallAccepted, PhoneConnection, \
    PhoneCall, UpdatePhoneCallSignalingData
from pyrogram.raw.types.phone import PhoneCall as PhonePhoneCall

from .aioudp import open_remote_endpoint
from .crypto import decrypt, EncryptionX, encrypt
from .packets.v2_4_4 import Packet, PacketHeader, PacketInit, PacketInitAck, PacketPing, PacketPong, PacketStreamData, \
    Stream
from .udp_endpoint import UdpEndpoint
from .utils import coro_with_additional_return, DhStuff, do_all_dh_stuff
from .utils._opus import _load_opus
from .utils._protocol import _make_protocol


class CallIdk_v2_4_4:
    OPUS = int.from_bytes(b"SUPO", "little", signed=False)

    def __init__(self, dh: DhStuff, call: PhonePhoneCall) -> None:
        self.dh = dh
        self.call = call
        self.key: bytes | None = None
        self.endpoints: list[UdpEndpoint] = []
        self.seq = 0
        self.remote_seq = 0
        self.opus_frames: list[bytes] | None = None
        self.sending_audio: int | None = None

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
            self.key = key = pow(g_b, self.dh.x, self.dh.prime).to_bytes(256, "big", signed=False)

            client.on_raw_update()(self._handle_signaling_update)

            self.call = await client.invoke(ConfirmCall(
                peer=self._make_input_call(),
                g_a=self.dh.g_x,
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
        if self.sending_audio is None or self.opus_frames is None:
            return
        if self.sending_audio >= len(self.opus_frames):
            self.sending_audio = None
            return

        self._send_stream_data(PacketStreamData(
            stream_id=1,
            pts=60 * self.sending_audio,
            extra_fec=False,
            keyframe=False,
            fragment_index=None,
            fragment_count=None,
            data=self.opus_frames[self.sending_audio],
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
                if self.opus_frames is None:
                    print("Loading opus frames...")
                    self.opus_frames = await asyncio.to_thread(_load_opus, "audio-mono.wav", 60)
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
        g_a_hash=dh.g_x_hash,
        protocol=_make_protocol("2.4.4"),
        video=False,
    ))

    call_idk = CallIdk_v2_4_4(dh, call)
    client.on_raw_update()(call_idk.handle_call_update)
