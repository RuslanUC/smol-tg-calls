from __future__ import annotations

import asyncio
import os
from hashlib import sha1
from io import BytesIO

import aioice
from aiortc import RTCRtpReceiver
from aiortc.clock import current_ms
from aiortc.rtcrtpparameters import RTCRtpReceiveParameters, RTCRtpCodecParameters
from aiortc.rtcrtpreceiver import RemoteStreamTrack
from aiortc.rtp import RtcpPacket, RtpPacket
from pyrogram import Client
from pyrogram.raw.functions.phone import RequestCall, ConfirmCall, SendSignalingData, AcceptCall
from pyrogram.raw.types import InputPhoneCall, UpdatePhoneCall, PhoneCallAccepted, PhoneConnection, \
    PhoneCall, UpdatePhoneCallSignalingData, PhoneConnectionWebrtc, PhoneCallRequested, PhoneCallWaiting, \
    PhoneCallDiscarded

from .aioudp import open_remote_endpoint
from .crypto import decrypt, EncryptionX, encrypt
from .packets.v2_7_7 import LegacySignalingPacket, CandidatesListMessage, AckMessage, AudioDataMessage, \
    VideoParametersMessage, RemoteMediaStateMessage
from .packets.v2_7_7.base import LegacySignalingPacketMessage
from .packets.v2_7_7.remote_media_state import RemoteVideoState, RemoteAudioState
from .udp_endpoint import UdpEndpoint
from .utils import coro_with_additional_return, do_all_dh_stuff, DhStuff
from .utils._protocol import _make_protocol

PhoneCallTypes = PhoneCallAccepted | PhoneCallRequested | PhoneCallWaiting


# constexpr uint32_t ssrcAudioIncoming = 1;
# constexpr uint32_t ssrcAudioOutgoing = 2;
# constexpr uint32_t ssrcAudioFecIncoming = 5;
# constexpr uint32_t ssrcAudioFecOutgoing = 6;
# constexpr uint32_t ssrcVideoIncoming = 3;
# constexpr uint32_t ssrcVideoOutgoing = 4;
# constexpr uint32_t ssrcVideoFecIncoming = 7;
# constexpr uint32_t ssrcVideoFecOutgoing = 8;


def is_payload_rtp(data: bytes) -> bool:
    return len(data) >= 12 and (data[0] >> 6) == 2 and (data[1] & 0x7f) < 64 or (data[1] & 0x7f) >= 96


def is_payload_rtcp(data: bytes) -> bool:
    return len(data) >= 4 and (data[0] >> 6) == 2 and 64 <= (data[1] & 0x7f) < 96


class FakeDtlsTransport:
    state = "open"

    def _register_rtp_receiver(self, receiver, parameters) -> None:
        ...


class CallIdk_v2_7_7o:
    OPUS = int.from_bytes(b"SUPO", "little", signed=False)

    def __init__(self, dh: DhStuff, call: PhoneCallTypes) -> None:
        self.dh = dh
        self.call = call
        self.key: bytes | None = None
        self.endpoints: list[UdpEndpoint] = []
        self.signaling_seq = 1
        self.remote_signaling_seq = 0
        self.transport_seq = 1
        self.remote_transport_seq = 0
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
        elif isinstance(update.phone_call, PhoneCallDiscarded):
            self.call = None
            await self.conn.close()

    async def _send_transport(self, packet: LegacySignalingPacket) -> None:
        assert self.key is not None

        await self.conn_ready.wait()
        assert self.conn is not None

        packet.seq = self.transport_seq
        self.transport_seq += 1

        print(f"sending (transport): {packet}")

        await self.conn.sendto(
            data=encrypt(packet.write(), self.key, EncryptionX.OUT_TRANSPORT, ctr=True),
            component=0,
        )

    async def _send_signaling(self, client: Client, packet: LegacySignalingPacket) -> None:
        assert self.key is not None

        packet.seq = self.signaling_seq
        self.signaling_seq += 1

        print(f"sending (signaling): {packet}")

        await client.invoke(SendSignalingData(
            peer=self._make_input_call(),
            data=encrypt(packet.write(), self.key, EncryptionX.OUT_SIGNALING, ctr=True),
        ))

    async def _ice_connected(self) -> None:
        assert self.key is not None
        assert self.conn is not None

        await self._send_transport(LegacySignalingPacket(
            seq=0,
            payload=VideoParametersMessage(
                aspect_ratio=0,
            ),
        ))
        await self._send_transport(LegacySignalingPacket(
            seq=0,
            payload=RemoteMediaStateMessage(
                video_state=RemoteVideoState.INACTIVE,
                audio_state=RemoteAudioState.ACTIVE,
            ),
        ))

        receiver = RTCRtpReceiver("audio", FakeDtlsTransport())
        receiver._track = track = RemoteStreamTrack("audio")
        await receiver.receive(RTCRtpReceiveParameters(
            codecs=[
                RTCRtpCodecParameters(
                    mimeType="audio/opus",
                    clockRate=48000,
                    channels=2,
                    payloadType=111,
                )
            ],
        ))

        while self.call is not None:
            current_time_ms = current_ms()
            try:
                data, component = await self.conn.recvfrom()
            except:
                break
            print(f"Received from component {component}: {data}")
            decrypted, valid = decrypt(data, self.key, x=EncryptionX.IN_TRANSPORT, ctr=True, ctr_value=0)
            print(f"  decrypted (valid={valid}): {decrypted}")
            packets = LegacySignalingPacket.read(BytesIO(decrypted))

            for packet in packets:
                skip = packet.seq <= self.remote_transport_seq
                skipped_text = ", skipped" if skip else ""
                print(f"    packet (transport{skipped_text}) = {packet}")

                if skip:
                    continue

                if packet.needs_ack:
                    self.remote_transport_seq = packet.seq
                    await self._send_transport(
                        LegacySignalingPacket(seq=0, payload=AckMessage(seq=packet.seq)),
                    )

                if isinstance(packet.payload, AudioDataMessage):
                    if is_payload_rtcp(packet.payload.data):
                        rtp = RtcpPacket.parse(packet.payload.data)
                        await receiver._handle_rtcp_packet(rtp)
                    else:
                        rtp = RtpPacket.parse(packet.payload.data)
                        await receiver._handle_rtp_packet(rtp, current_time_ms)
                    print(f"      rtp: {rtp}")
                    # idk = RTP().fromBytearray(bytearray(packet.payload.data))
                    # print(f"      rtp: seq={idk.sequenceNumber}, ssrc={idk.ssrc}, type={idk.payloadType}, payload={idk.payload}")

                # task = asyncio.create_task(self._handle_signaling(packet.payload))
                # self.tasks.add(task)
                # task.add_done_callback(self.tasks.discard)

        print(await track.recv())

    def _ice_connected_callback(self, _: asyncio.Task) -> None:
        task = asyncio.create_task(self._ice_connected())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _handle_signaling(self, payload: LegacySignalingPacketMessage) -> None:
        if isinstance(payload, CandidatesListMessage):
            await self.conn_ready.wait()
            assert self.conn is not None

            self.conn.remote_username = payload.ufrag
            self.conn.remote_password = payload.pwd
            if not self.connected:
                self.connected = True
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

        await self.conn_ready.wait()

        print(f"got signaling data (len={len(update.data)}): {update.data}")
        decrypted, valid = decrypt(update.data, self.key, x=EncryptionX.IN_SIGNALING, ctr=True, ctr_value=0)
        print(f"  decrypted (valid={valid}): {decrypted}")
        if valid:
            packets = LegacySignalingPacket.read(BytesIO(decrypted))
            for packet in packets:
                skip = packet.seq <= self.remote_signaling_seq
                skipped_text = ", skipped" if skip else ""
                print(f"    packet (signaling{skipped_text}) = {packet}")

                if skip:
                    continue

                if packet.needs_ack:
                    self.remote_signaling_seq = packet.seq
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
        turn_servers = []
        stun_servers = []
        connection: PhoneConnection | PhoneConnectionWebrtc
        for connection in self.call.connections:
            ips.append(connection.ip)
            if isinstance(connection, PhoneConnectionWebrtc):
                if connection.stun:
                    stun_servers.append(aioice.StunServer(connection.ip, connection.port))
                if connection.turn:
                    turn_servers.append(aioice.TurnServer(
                        connection.ip,
                        connection.port,
                        connection.username,
                        connection.password,
                    ))
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
            stun_servers=stun_servers,
            turn_servers=turn_servers,
            transport_policy=aioice.TransportPolicy.RELAY,
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
