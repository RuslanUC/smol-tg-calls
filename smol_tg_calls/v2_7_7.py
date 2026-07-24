from __future__ import annotations

import asyncio
import os
import struct
import wave
from abc import ABC
from hashlib import sha256, sha1
from io import BytesIO
from typing import NamedTuple, Self

import aioice
import opuslib
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer
from pyrogram import Client
from pyrogram.raw.functions.messages import GetDhConfig
from pyrogram.raw.functions.phone import RequestCall, ConfirmCall, SendSignalingData, AcceptCall
from pyrogram.raw.types import InputPhoneCall, UpdatePhoneCall, PhoneCallAccepted, PhoneCallProtocol, PhoneConnection, \
    PhoneCall, UpdatePhoneCallSignalingData, PhoneConnectionWebrtc, PhoneCallRequested, PhoneCallWaiting
from pyrogram.raw.types.phone import PhoneCall as PhonePhoneCall

from .aioudp import open_remote_endpoint
from .crypto import decrypt, EncryptionX, encrypt
from .packets import Packet, PacketHeader, PacketInit, PacketInitAck, PacketPing, PacketPong, PacketStreamData, \
    PacketBase, PacketPayloadBase
from .packets.init_ack import Stream
from .udp_endpoint import UdpEndpoint
from .utils import coro_with_additional_return, do_all_dh_stuff, DhStuff, uint_le_from_bytes, uint_be_from_bytes, \
    u32be_to_bytes, u8be_to_bytes
from .utils._opus import _load_opus
from .utils._protocol import _make_protocol

PhoneCallTypes = PhoneCallAccepted | PhoneCallRequested | PhoneCallWaiting


class LegacySignalingPacketMessage(PacketPayloadBase, ABC):
    REQUIRES_ACK: bool

    __slots__ = ()


class LegacySignalingPacket(PacketBase):
    __slots__ = ("seq", "needs_ack", "packet_type", "payload",)

    def __init__(
            self, seq: int, payload: LegacySignalingPacketMessage | bytes, needs_ack: bool = False,
            packet_type: int | None = None,
    ) -> None:
        self.seq = seq
        self.needs_ack = needs_ack
        self.packet_type = packet_type
        self.payload = payload

    @classmethod
    def read(cls, data: BytesIO) -> list[LegacySignalingPacket]:
        result = []

        cur = data.tell()
        total_size = data.seek(0, os.SEEK_END)
        data.seek(cur)

        while data.tell() < total_size:
            seq = uint_be_from_bytes(data.read(4))
            # single_message = bool(seq & (1 << 31))
            needs_ack = bool(seq & (1 << 30))
            seq &= 0x3fffffff

            packet_type = uint_le_from_bytes(data.read(1))
            if packet_type == 1:
                payload = CandidatesListMessage.read(data)
            elif packet_type == 2:
                payload = VideoFormatsMessage.read(data)
            elif packet_type == 254:
                payload = EmptyMessage.read(data)
            elif packet_type == 255:
                payload = AckMessage.read(data)
            else:
                payload = data.read()

            result.append(LegacySignalingPacket(
                seq=seq,
                needs_ack=needs_ack,
                payload=payload,
                packet_type=packet_type,
            ))

        return result

    def write(self) -> bytes:
        if isinstance(self.payload, LegacySignalingPacketMessage):
            self.packet_type = self.payload.PACKET_TYPE
            self.needs_ack = self.payload.REQUIRES_ACK
            payload = self.payload.write()
        else:
            payload = self.payload

        seq = self.seq
        # if self.single_message:
        #     seq |= 1 << 31
        if self.needs_ack:
            seq |= 1 << 30

        assert self.packet_type is not None

        return b"".join([
            u32be_to_bytes(seq),
            u8be_to_bytes(self.packet_type),
            payload,
        ])


class StringValue(PacketBase):
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    @classmethod
    def read(cls, data: BytesIO) -> StringValue:
        length = uint_be_from_bytes(data.read(4))
        string_bytes = data.read(length)
        return StringValue(string_bytes.decode("utf8"))

    def write(self) -> bytes:
        string_bytes = self.value.encode("utf8")
        return u32be_to_bytes(len(string_bytes)) + string_bytes


class CandidatesListMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 1
    REQUIRES_ACK = True

    __slots__ = ("candidates", "ufrag", "pwd")

    def __init__(self, candidates: list[str], ufrag: str, pwd: str) -> None:
        self.candidates = candidates
        self.ufrag = ufrag
        self.pwd = pwd

    @classmethod
    def read(cls, data: BytesIO) -> CandidatesListMessage:
        candidates_num = uint_be_from_bytes(data.read(1))
        candidates = []
        for _ in range(candidates_num):
            candidates.append(StringValue.read(data).value)

        ufrag = StringValue.read(data).value
        pwd = StringValue.read(data).value

        return CandidatesListMessage(
            candidates=candidates,
            ufrag=ufrag,
            pwd=pwd,
        )

    def write(self) -> bytes:
        return b"".join([
            u8be_to_bytes(len(self.candidates)),
            *(
                StringValue(candidate).write()
                for candidate in self.candidates
            ),
            StringValue(self.ufrag).write(),
            StringValue(self.pwd).write(),
        ])


class VideoFormatsMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 2
    REQUIRES_ACK = True

    __slots__ = ("formats", "encoders_count",)

    def __init__(self, formats: dict[str, dict[str, str]], encoders_count: int) -> None:
        self.formats = formats
        self.encoders_count = encoders_count

    @classmethod
    def read(cls, data: BytesIO) -> VideoFormatsMessage:
        formats_num = uint_be_from_bytes(data.read(1))
        formats = {}
        for _ in range(formats_num):
            name = StringValue.read(data).value
            params_num = uint_be_from_bytes(data.read(1))
            params = {}
            for _ in range(params_num):
                key = StringValue.read(data).value
                value = StringValue.read(data).value
                params[key] = value
            formats[name] = params

        encoders_count = uint_be_from_bytes(data.read(1))

        return VideoFormatsMessage(
            formats=formats,
            encoders_count=encoders_count,
        )

    def write(self) -> bytes:
        chunks = [u8be_to_bytes(len(self.formats))]

        for name, params in self.formats.items():
            chunks.append(StringValue(name).write())
            chunks.append(u8be_to_bytes(len(params)))
            for key, value in params.items():
                chunks.append(StringValue(key).write())
                chunks.append(StringValue(value).write())

        chunks.append(u8be_to_bytes(self.encoders_count))

        return b"".join(chunks)


class EmptyMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 254
    REQUIRES_ACK = False

    __slots__ = ()

    def __init__(self) -> None:
        ...

    @classmethod
    def read(cls, data: BytesIO) -> EmptyMessage:
        return EmptyMessage()

    def write(self) -> bytes:
        return b""


class AckMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 255
    REQUIRES_ACK = False

    __slots__ = ("seq",)

    def __init__(self, seq: int) -> None:
        self.seq = seq

    @classmethod
    def read(cls, data: BytesIO) -> AckMessage:
        seq = uint_be_from_bytes(data.read(4))
        return AckMessage(
            seq=seq,
        )

    def write(self) -> bytes:
        return u32be_to_bytes(self.seq)


class CallIdk_v2_7_7o:
    OPUS = int.from_bytes(b"SUPO", "little", signed=False)

    def __init__(self, dh: DhStuff, call: PhoneCallTypes) -> None:
        self.dh = dh
        self.call = call
        self.key: bytes | None = None
        self.endpoints: list[UdpEndpoint] = []
        self.seq = 1
        self.remote_seq = 0
        self.opus_frames: list[bytes] | None = None
        self.sending_audio: int | None = None
        self.conn: aioice.Connection | None = None
        self.conn_ready = asyncio.Event()
        self.tasks = set()
        self.connected = False

    def _make_input_call(self) -> InputPhoneCall:
        return InputPhoneCall(
            id=self.call.id,
            access_hash=self.call.access_hash,
        )

    async def handle_raw_update(self, client: Client, update: UpdatePhoneCall, _1, _2) -> None:
        if not isinstance(update, UpdatePhoneCall):
            if isinstance(update, UpdatePhoneCallSignalingData):
                await self._handle_signaling_update(client, update, _1, _2)
            return
        if update.phone_call.id != self.call.id:
            return
        print(update)
        self.call = update.phone_call
        if isinstance(update.phone_call, PhoneCallAccepted):
            g_b = int.from_bytes(update.phone_call.g_b, "big", signed=False)
            self.key = key = pow(g_b, self.dh.x, self.dh.prime).to_bytes(256, "big", signed=False)

            result = await client.invoke(ConfirmCall(
                peer=self._make_input_call(),
                g_a=self.dh.g_x,
                key_fingerprint=int.from_bytes(sha1(key).digest()[-8:], "little", signed=True),
                protocol=_make_protocol("2.7.7"),
            ))
            self.call = result.phone_call

            print(self.call)

            await self._call_ice(client)

    async def _send_signaling(self, client: Client, packet: LegacySignalingPacket) -> None:
        assert self.key is not None

        packet.seq = self.seq
        self.seq += 1

        print(f"sending: {packet}")

        await client.invoke(SendSignalingData(
            peer=self._make_input_call(),
            data=encrypt(packet.write(), self.key, EncryptionX.OUT_SIGNALING, ctr=True),
        ))

    async def _ice_connected(self) -> None:
        assert self.key is not None
        assert self.conn is not None

        while True:
            data, component = await self.conn.recvfrom()
            print(f"Received from component {component}: {data}")
            decrypted, valid = decrypt(data, self.key, x=EncryptionX.IN_TRANSPORT, ctr=True, ctr_value=0)
            print(f"  decrypted (valid={valid}): {decrypted}")

    def _ice_connected_callback(self, _: asyncio.Task) -> None:
        task = asyncio.create_task(self._ice_connected())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _handle_signaling(self, payload: LegacySignalingPacketMessage) -> None:
        assert self.conn is not None

        if isinstance(payload, CandidatesListMessage):
            self.conn.remote_username = payload.ufrag
            self.conn.remote_password = payload.pwd
            if not self.connected:
                self.connected = True
                await self.conn_ready.wait()
                task = asyncio.create_task(self.conn.connect())
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
                task.add_done_callback(self._ice_connected_callback)

            for candidate_sdp in payload.candidates:
                if not candidate_sdp.startswith("candidate:"):
                    continue
                candidate_sdp = candidate_sdp[10:]
                candidate = aioice.Candidate.from_sdp(candidate_sdp)
                if candidate.host.endswith(".reflector"):
                    # No clue how to work with these
                    continue
                print(f"Adding remote candidate {candidate}")
                await self.conn.add_remote_candidate(candidate)
        elif isinstance(payload, AckMessage):
            print(f"got ack for message {payload.seq}")

    async def _handle_signaling_update(self, client: Client, update: UpdatePhoneCallSignalingData, _1, _2) -> None:
        assert self.key is not None
        assert self.conn is not None

        print(f"got signaling data (len={len(update.data)}): {update.data}")
        decrypted, valid = decrypt(update.data, self.key, x=EncryptionX.IN_SIGNALING, ctr=True, ctr_value=0)
        print(f"  decrypted (valid={valid}): {decrypted}")
        if valid:
            packets = LegacySignalingPacket.read(BytesIO(decrypted))
            for packet in packets:
                skip = packet.seq <= self.remote_seq
                skipped_text = "(skipped) " if skip else ""
                print(f"    packet {skipped_text}= {packet}")

                if skip:
                    continue

                if packet.needs_ack:
                    self.remote_seq = packet.seq
                    await self._send_signaling(
                        client,
                        LegacySignalingPacket(seq=0, payload=AckMessage(seq=packet.seq)),
                    )

                task = asyncio.create_task(self._handle_signaling(packet.payload))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def _call_ice(self, client: Client) -> None:
        assert self.key is not None and self.call is not None

        ips = []
        ice_servers = []
        turn_server = None
        stun_server = None
        turn_username = None
        turn_password = None
        connection: PhoneConnection | PhoneConnectionWebrtc
        for connection in self.call.connections:
            ips.append(connection.ip)
            if isinstance(connection, PhoneConnectionWebrtc):
                if connection.stun:
                    stun_server = (connection.ip, connection.port)
                if connection.turn:
                    turn_server = (connection.ip, connection.port)
                    turn_username = connection.username
                    turn_password = connection.password
                # if connection.stun:
                #     ice_servers.append(RTCIceServer(f"stun:{connection.id}:{connection.port}"))
                # if connection.turn:
                #     ice_servers.append(RTCIceServer(
                #         f"turn:{connection.id}:{connection.port}",
                #         username=connection.username,
                #         credential=connection.password,
                #     ))

        # rtc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))

        self.conn = conn = aioice.Connection(
            ice_controlling=True,
            stun_server=stun_server,
            turn_server=turn_server,
            turn_username=turn_username,
            turn_password=turn_password,
        )
        conn._components = {0}
        await conn.gather_candidates()

        # print(f"Wireshark filter: `udp && ({' || '.join([f'ip.addr == {ip}' for ip in ips])})`")

        await self._send_signaling(
            client,
            LegacySignalingPacket(
                seq=0,
                payload=CandidatesListMessage(
                    candidates=[
                        f"candidate:{candidate.to_sdp()}"
                        for candidate in conn.local_candidates
                        if candidate.type == "relay"
                    ],
                    ufrag=conn.local_username,
                    pwd=conn.local_password,
                ),
            ),
        )

        self.conn_ready.set()

    async def _req_reflector_peer_self_info(self) -> None:
        await asyncio.gather(*(
            endpoint.get_self_info()
            for endpoint in self.endpoints
        ))

    def _send_to_all(self, data: bytes) -> None:
        for endpoint in self.endpoints:
            endpoint.send(data)


class CallIdk_v2_7_7i:
    OPUS = int.from_bytes(b"SUPO", "little", signed=False)

    def __init__(self, dh: DhStuff , call: PhoneCallTypes | None) -> None:
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
            id=self.call.id,
            access_hash=self.call.access_hash,
        )

    async def handle_call_update(self, client: Client, update: UpdatePhoneCall, _1, _2) -> None:
        if not isinstance(update, UpdatePhoneCall):
            print("what", update)
            return
        if self.call is None and isinstance(update.phone_call, PhoneCallRequested):
            self.call = update.phone_call
        if update.phone_call.id != self.call.id:
            return
        self.call = update.phone_call
        print(update)
        if isinstance(update.phone_call, PhoneCallRequested):
            client.on_raw_update()(self._handle_signaling_update)

            result = await client.invoke(AcceptCall(
                peer=self._make_input_call(),
                g_b=self.dh.g_x,
                protocol=_make_protocol("2.7.7"),
            ))
            self.call = result.phone_call

            print(self.call)
        elif isinstance(update.phone_call, PhoneCall):
            g_a = int.from_bytes(update.phone_call.g_a_or_b, "big", signed=False)
            self.key = pow(g_a, self.dh.x, self.dh.prime).to_bytes(256, "big", signed=False)

            await self._call_udp()
            # await self._call_webrtc(client)

    async def _handle_signaling_update(self, client: Client, update: UpdatePhoneCallSignalingData, _1, _2) -> None:
        if not isinstance(update, UpdatePhoneCallSignalingData):
            print("update btw", update.__class__)
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

    async def _call_udp(self) -> None:
        assert self.key is not None

        self.endpoints.clear()

        call: PhoneCall = self.call
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

        """
        turn_server = None
        stun_server = None
        turn_username = None
        turn_password = None
        connection: PhoneConnection | PhoneConnectionWebrtc
        for connection in self.call.phone_call.connections:
            if isinstance(connection, PhoneConnectionWebrtc):
                if connection.stun:
                    stun_server = (connection.ip, connection.port)
                if connection.turn:
                    turn_server = (connection.ip, connection.port)
                    turn_username = connection.username
                    turn_password = connection.password

        conn = aioice.Connection(
            ice_controlling=False,
            stun_server=stun_server,
            turn_server=turn_server,
            turn_username=turn_username,
            turn_password=turn_password,
        )
        await conn.gather_candidates()

        sdps = [f"candidate:{candidate.to_sdp()}".encode("utf8") for candidate in conn.local_candidates]
        ufrag = conn.local_username.encode("utf8")
        pwd = conn.local_password.encode("utf8")

        print(sdps)
        print(ufrag)
        print(pwd)

        signaling_ts = b"".join([
            (1 | (1 << 31) | (1 << 30)).to_bytes(4, "big", signed=False),  # seq + flags
            (1).to_bytes(1),  # message type
            len(sdps).to_bytes(1),
            *(
                len(sdp).to_bytes(4, "big", signed=False) + sdp
                for sdp in sdps
            ),
            len(ufrag).to_bytes(4, "big", signed=False),
            ufrag,
            len(pwd).to_bytes(4, "big", signed=False),
            pwd,
        ])

        self._send_to_all(encrypt(signaling_ts, self.key, EncryptionX.OUT_SIGNALING, ctr=True))
        """

        self.seq = 0
        self.remote_seq = 0
        # for _ in range(8):
        #     self._send_init()

        print("Waiting for remote packet...")
        done, pending = await asyncio.wait([
            asyncio.create_task(coro_with_additional_return(endpoint.receive(), endpoint))
            for endpoint in self.endpoints
        ], return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            if not task.done():
                task.cancel()

        data, endpoint = await list(done)[0]
        print(data)
        # await self._process_packet(data)

        while True:
            data = await endpoint.receive()
            print(data)
            # await self._process_packet(data)


async def _call_outgoing_v2_7_7(client: Client) -> None:
    dh = await do_all_dh_stuff(client)

    call = await client.invoke(RequestCall(
        user_id=await client.resolve_peer(os.environ["CALL_PEER"]),
        random_id=int.from_bytes(os.urandom(4), "big", signed=True),
        g_a_hash=dh.g_x_hash,
        protocol=_make_protocol("2.7.7"),
        video=False,
    ))

    call_idk = CallIdk_v2_7_7o(dh, call.phone_call)
    # call_idk = CallIdk_v2_7_7i(dh, None)
    client.on_raw_update()(call_idk.handle_raw_update)
