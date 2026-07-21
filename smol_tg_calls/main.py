from __future__ import annotations

import asyncio
import os
import socket
import wave
from abc import ABC, abstractmethod
from io import BytesIO
from typing import NamedTuple, Self, Awaitable, TypeVar
from hashlib import sha256, sha1

import opuslib
import tgcrypto
from pyrogram import Client
from pyrogram.raw.functions.messages import GetDhConfig
from pyrogram.raw.functions.phone import RequestCall, ConfirmCall
from pyrogram.raw.types import InputPhoneCall, UpdatePhoneCall, PhoneCallAccepted, PhoneCallProtocol, PhoneConnection, \
    PhoneCall, UpdatePhoneCallSignalingData
from pyrogram.raw.types.phone import PhoneCall as PhonePhoneCall

from .aioudp import RemoteEndpoint, open_remote_endpoint


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


def kdf(msg_key: bytes, key: bytes, x: int, ctr: bool) -> tuple[bytes, bytes]:
    sha256_a = sha256(msg_key + key[x:x + 36]).digest()
    sha256_b = sha256(key[40 + x:40 + x + 36] + msg_key).digest()
    aes_key = sha256_a[0:0 + 8] + sha256_b[8:8 + 16] + sha256_a[24:24 + 8]
    if ctr:
        aes_iv = sha256_b[0:0 + 4] + sha256_a[8:8 + 8] + sha256_b[24:24 + 4]
    else:
        aes_iv = sha256_b[0:0 + 8] + sha256_a[8:8 + 16] + sha256_b[24:24 + 8]

    return aes_key, aes_iv


class EncryptionX:
    OUT_TRANSPORT = 0
    IN_TRANSPORT = 8
    OUT_SIGNALING = 128
    IN_SIGNALING = 136


def encrypt(data: bytes, key: bytes, x: int, ctr: bool = False, ctr_value: int = 0) -> bytes:
    msg_key_large = sha256(key[88 + x:88 + x + 32] + data).digest()
    msg_key = msg_key_large[8:8 + 16]

    aes_key, aes_iv = kdf(msg_key, key, x, ctr)

    if ctr:
        state = bytearray(1)
        state[0] = ctr_value
        encrypted = tgcrypto.ctr256_encrypt(data, aes_key, aes_iv, state)
    else:
        encrypted = tgcrypto.ige256_encrypt(data, aes_key, aes_iv)

    return msg_key + encrypted


def decrypt(data: bytes, key: bytes, x: int, ctr: bool = False, ctr_value: int = 0) -> tuple[bytes, bool]:
    msg_key, data = data[:16], data[16:]

    aes_key, aes_iv = kdf(msg_key, key, x, ctr)

    if ctr:
        state = bytearray(1)
        state[0] = ctr_value
        decrypted = tgcrypto.ctr256_decrypt(data, aes_key, aes_iv, state)
    else:
        decrypted = tgcrypto.ige256_decrypt(data, aes_key, aes_iv)

    check_msg_key_large = sha256(key[88 + x:88 + x + 32] + decrypted).digest()
    check_msg_key = check_msg_key_large[8:8 + 16]

    return decrypted, msg_key == check_msg_key


T1 = TypeVar("T1")
T2 = TypeVar("T2")


async def coro_with_additional_return(coro: Awaitable[T1], add: T2) -> tuple[T1, T2]:
    return await coro, add


class UdpEndpoint:
    def __init__(self, sock: RemoteEndpoint, peer_tag: bytes) -> None:
        self.sock = sock
        self.peer_tag = peer_tag

    def send(self, data: bytes) -> None:
        self.sock.send(self.peer_tag + data)

    async def receive(self) -> bytes:
        data = await self.sock.receive()
        assert data.startswith(self.peer_tag)
        return data[len(self.peer_tag):]

    async def get_self_info(self) -> None:
        self.send(
            b""
            + b"\xff" * 12
            + b"\xfe"
            + b"\xff" * 3
            + (123).to_bytes(8, "little", signed=False)
        )
        self_info = await self.receive()
        reader = BytesIO(self_info)
        assert reader.read(12) == b"\xff" * 12

        reflector_constructor = int.from_bytes(reader.read(4), "little", signed=False)
        reflector_time = int.from_bytes(reader.read(4), "little", signed=False)
        reflector_query_id = int.from_bytes(reader.read(8), "little", signed=False)
        reflector_address = reader.read(16)
        reflector_port = int.from_bytes(reader.read(4), "little", signed=False)

        print(f"Reflector constructor: {hex(reflector_constructor)}")
        print(f"Reflector time: {reflector_time}")
        print(f"Reflector query id: {reflector_query_id}")
        print(f"Reflector our address raw: {reflector_address}")
        if reflector_constructor == 0xc01572c7:
            print(f"Reflector our address: {socket.inet_ntoa(reflector_address[-4:])}")
        print(f"Reflector our port: {reflector_port}")
        print(f"Leftover bytes: {reader.read()}")
        print("=" * 32)


class SlotsRepr:
    __slots__ = ()

    def __repr__(self) -> str:
        slots = set()
        for cls in self.__class__.mro():
            slots.update(getattr(cls, "__slots__", ()))

        fields = ", ".join([f"{slot}={getattr(self, slot)!r}" for slot in slots])
        return f"{self.__class__.__name__}({fields})"


class IdkPacketHeader(SlotsRepr):
    __slots__ = ("length", "packet_type", "remote_seq", "seq", "acks", "flags",)

    def __init__(
            self, *, packet_type: int, remote_seq: int, seq: int, acks: int, length: int = 0, flags: int = 0,
    ) -> None:
        self.length = length
        self.packet_type = packet_type
        self.remote_seq = remote_seq
        self.seq = seq
        self.acks = acks
        self.flags = flags

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacketHeader:
        return IdkPacketHeader(
            length=int.from_bytes(data.read(2), "little", signed=False),
            packet_type=int.from_bytes(data.read(1), "little", signed=False),
            remote_seq=int.from_bytes(data.read(4), "little", signed=False),
            seq=int.from_bytes(data.read(4), "little", signed=False),
            acks=int.from_bytes(data.read(4), "little", signed=False),
            flags=int.from_bytes(data.read(1), "little", signed=False),
        )

    def write(self) -> bytes:
        return b"".join([
            self.length.to_bytes(2, "little", signed=False),
            self.packet_type.to_bytes(1),
            self.remote_seq.to_bytes(4, "little", signed=False),
            self.seq.to_bytes(4, "little", signed=False),
            self.acks.to_bytes(4, "little", signed=False),
            self.flags.to_bytes(1),
        ])


class IdkPacket(SlotsRepr):
    __slots__ = ("header", "payload",)

    def __init__(self, header: IdkPacketHeader, payload: IdkPacketPayload | bytes) -> None:
        self.header = header
        self.payload = payload

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacket:
        header = IdkPacketHeader.read(data)
        payload = data.read(header.length - 16)
        if header.packet_type == 1:
            payload = IdkPacketInit.read(BytesIO(payload))
        elif header.packet_type == 2:
            payload = IdkPacketInitAck.read(BytesIO(payload))
        elif header.packet_type == 4:
            payload = IdkPacketStreamData.read(BytesIO(payload))
        elif header.packet_type == 6:
            payload = IdkPacketPing.read(BytesIO(payload))
        elif header.packet_type == 7:
            payload = IdkPacketPong.read(BytesIO(payload))

        return IdkPacket(header, payload)

    def write(self, pad: bool = False) -> bytes:
        if isinstance(self.payload, IdkPacketPayload):
            self.header.packet_type = self.payload.PACKET_TYPE
            payload_bytes = self.payload.write()
        else:
            payload_bytes = self.payload
        self.header.length = 16 + len(payload_bytes)
        result = self.header.write() + payload_bytes
        if pad and self.header.length % 16 != 0:
            padding = (-len(result)) % 16
            if padding < 16:
                padding += 16
            result += os.urandom(padding)
        return result


class IdkPacketPayload(ABC, SlotsRepr):
    PACKET_TYPE: int

    __slots__ = ()

    @classmethod
    @abstractmethod
    def read(cls, data: BytesIO) -> Self:
        ...

    @abstractmethod
    def write(self) -> bytes:
        ...


class IdkPacketInit(IdkPacketPayload):
    PACKET_TYPE = 1

    __slots__ = ("version", "min_version", "flags", "audio_streams", "video_streams",)

    def __init__(
            self, version: int, min_version: int, flags: int, audio_streams: list[int], video_streams: list[int],
    ) -> None:
        self.version = version
        self.min_version = min_version
        self.flags = flags
        self.audio_streams = audio_streams
        self.video_streams = video_streams

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacketInit:
        version = int.from_bytes(data.read(4), "little", signed=False)
        min_version = int.from_bytes(data.read(4), "little", signed=False)
        flags = int.from_bytes(data.read(4), "little", signed=False)
        audio_streams_count = int.from_bytes(data.read(1))
        audio_streams = [
            int.from_bytes(data.read(4), "little", signed=False)
            for _ in range(audio_streams_count)
        ]
        video_streams_count = int.from_bytes(data.read(1))
        video_streams = [
            int.from_bytes(data.read(4), "little", signed=False)
            for _ in range(video_streams_count)
        ]
        data.read(1)

        return cls(
            version=version,
            min_version=min_version,
            flags=flags,
            audio_streams=audio_streams,
            video_streams=video_streams,
        )

    def write(self) -> bytes:
        return b"".join([
            self.version.to_bytes(4, "little", signed=False),
            self.min_version.to_bytes(4, "little", signed=False),
            self.flags.to_bytes(4, "little", signed=False),
            len(self.audio_streams).to_bytes(1),
            b"".join((
                stream.to_bytes(4, "little", signed=False)
                for stream in self.audio_streams
            )),
            len(self.video_streams).to_bytes(1),
            b"".join((
                stream.to_bytes(4, "little", signed=False)
                for stream in self.video_streams
            )),
            b"\x00",  # TODO: what is this?
        ])


class IdkStream(IdkPacketPayload):
    PACKET_TYPE = 256

    __slots__ = ("id", "type", "codec", "frame_duration", "enabled",)

    def __init__(
            self, stream_id: int, stream_type: int, codec: int, frame_duration: int, enabled: int,
    ) -> None:
        self.id = stream_id
        self.type = stream_type
        self.codec = codec
        self.frame_duration = frame_duration
        self.enabled = enabled

    @classmethod
    def read(cls, data: BytesIO) -> IdkStream:
        stream_id = int.from_bytes(data.read(1))
        stream_type = int.from_bytes(data.read(1))
        codec = int.from_bytes(data.read(4))
        frame_duration = int.from_bytes(data.read(2), "little", signed=False)
        enabled = int.from_bytes(data.read(1))

        return cls(
            stream_id=stream_id,
            stream_type=stream_type,
            codec=codec,
            frame_duration=frame_duration,
            enabled=enabled,
        )

    def write(self) -> bytes:
        return b"".join([
            self.id.to_bytes(1),
            self.type.to_bytes(1),
            self.codec.to_bytes(4),
            self.frame_duration.to_bytes(2, "little", signed=False),
            self.enabled.to_bytes(1),
        ])


class IdkPacketInitAck(IdkPacketPayload):
    PACKET_TYPE = 2

    __slots__ = ("version", "min_version", "streams",)

    def __init__(
            self, version: int, min_version: int, streams: list[IdkStream],
    ) -> None:
        self.version = version
        self.min_version = min_version
        self.streams = streams

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacketInitAck:
        version = int.from_bytes(data.read(4), "little", signed=False)
        min_version = int.from_bytes(data.read(4), "little", signed=False)
        streams_count = int.from_bytes(data.read(1))
        streams = [
            IdkStream.read(data)
            for _ in range(streams_count)
        ]

        return cls(
            version=version,
            min_version=min_version,
            streams=streams,
        )

    def write(self) -> bytes:
        return b"".join([
            self.version.to_bytes(4, "little", signed=False),
            self.min_version.to_bytes(4, "little", signed=False),
            len(self.streams).to_bytes(1),
            b"".join((
                stream.write()
                for stream in self.streams
            )),
        ])


class IdkPacketStreamData(IdkPacketPayload):
    PACKET_TYPE = 4

    __slots__ = ("stream_id", "pts", "extra_fec", "keyframe", "fragment_index", "fragment_count", "data",)

    def __init__(
            self, stream_id: int, pts: int, extra_fec: bool, keyframe: bool,
            fragment_index: int | None, fragment_count: int | None, data: bytes,
    ) -> None:
        self.stream_id = stream_id
        self.pts = pts
        self.extra_fec = extra_fec
        self.keyframe = keyframe
        self.fragment_index = fragment_index
        self.fragment_count = fragment_count
        self.data = data

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacketStreamData:
        stream_id = int.from_bytes(data.read(1))
        flags = stream_id & 0xc0
        stream_id &= 0x3f
        if flags & 0x40:
            sdlen = int.from_bytes(data.read(2), "little", signed=False)
        else:
            sdlen = int.from_bytes(data.read(1), signed=False)
        pts = int.from_bytes(data.read(4), "little", signed=False)
        fragmented = bool(sdlen & (1 << 14))
        extra_fec = bool(sdlen & (1 << 13))
        keyframe = bool(sdlen & (1 << 15))
        fragment_index = fragment_count = None
        if fragmented:
            fragment_index = int.from_bytes(data.read(1))
            fragment_count = int.from_bytes(data.read(1))
        sdlen &= 0x0f77
        media = data.read(sdlen)
        return cls(
            stream_id=stream_id,
            pts=pts,
            extra_fec=extra_fec,
            keyframe=keyframe,
            fragment_index=fragment_index,
            fragment_count=fragment_count,
            data=media,
        )

    def write(self) -> bytes:
        sdlen = len(self.data)
        fragment_idx_cnt = b""
        if self.fragment_index and self.fragment_count:
            fragment_idx_cnt = (
                    self.fragment_index.to_bytes(1, signed=False)
                    + self.fragment_count.to_bytes(1, signed=False)
            )
            sdlen |= 1 << 14
        if self.extra_fec:
            sdlen |= 1 << 13
        if self.keyframe:
            sdlen |= 1 << 15
        stream_id = self.stream_id
        if sdlen > 255:
            stream_id |= 0x40

        return (
                b""
                + stream_id.to_bytes(1, signed=False)
                + sdlen.to_bytes(2 if sdlen > 255 else 1, "little", signed=False)
                + self.pts.to_bytes(4, "little", signed=False)
                + fragment_idx_cnt
                + self.data
        )


class IdkPacketPing(IdkPacketPayload):
    PACKET_TYPE = 6

    __slots__ = ()

    def __init__(self) -> None:
        ...

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacketPing:
        return cls()

    def write(self) -> bytes:
        return b""


class IdkPacketPong(IdkPacketPayload):
    PACKET_TYPE = 7

    __slots__ = ("out_seq",)

    def __init__(self, out_seq: int) -> None:
        self.out_seq = out_seq

    @classmethod
    def read(cls, data: BytesIO) -> IdkPacketPong:
        out_seq = int.from_bytes(data.read(4), "little", signed=False)
        return cls(out_seq=out_seq)

    def write(self) -> bytes:
        return self.out_seq.to_bytes(4, "little", signed=False)


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

            dec = client.on_raw_update()(self._handle_signaling_update)

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
        packet_to_send = IdkPacket(
            header=IdkPacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=IdkPacketInit(
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
        packet_to_send = IdkPacket(
            header=IdkPacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=IdkPacketInitAck(
                version=9,
                min_version=9,
                streams=[
                    IdkStream(
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
        packet_to_send = IdkPacket(
            header=IdkPacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=IdkPacketPing()
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_pong(self) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = IdkPacket(
            header=IdkPacketHeader(
                packet_type=0,
                remote_seq=self.remote_seq,
                seq=self.seq,
                acks=0,
            ),
            payload=IdkPacketPong(self.remote_seq)
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, EncryptionX.OUT_TRANSPORT))

    def _send_stream_data(self, data: IdkPacketStreamData) -> None:
        assert self.key is not None

        self.seq += 1
        packet_to_send = IdkPacket(
            header=IdkPacketHeader(
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

        self._send_stream_data(IdkPacketStreamData(
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
        packet = IdkPacket.read(BytesIO(decrypted))
        if packet.header.seq <= self.remote_seq:
            return
        self.remote_seq = packet.header.seq

        print(f"received: {packet!r}")
        payload = packet.payload

        if isinstance(payload, IdkPacketInit):
            self._send_init_ack()
        elif isinstance(payload, IdkPacketInitAck):
            self._send_init()
        elif isinstance(payload, IdkPacketPing):
            self._send_pong()
        elif isinstance(payload, IdkPacketStreamData):
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