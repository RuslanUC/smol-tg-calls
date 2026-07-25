from __future__ import annotations

import asyncio
from io import BytesIO
from typing import TYPE_CHECKING

import aioice
from aiortc import RTCRtpReceiver
from aiortc.clock import current_ms
from aiortc.rtcrtpparameters import RTCRtpReceiveParameters, RTCRtpCodecParameters
from aiortc.rtcrtpreceiver import RemoteStreamTrack
from aiortc.rtp import RtcpPacket, RtpPacket
from av.frame import Frame
from pyrogram import Client
from pyrogram.raw.functions.phone import SendSignalingData
from pyrogram.raw.types import PhoneCallAccepted, PhoneConnection, \
    UpdatePhoneCallSignalingData, PhoneConnectionWebrtc, PhoneCallRequested, PhoneCallWaiting

from .base import PhoneCallProtocol
from ..crypto import decrypt, encrypt
from ..packets.v2_7_7 import LegacySignalingPacket, CandidatesListMessage, AckMessage, AudioDataMessage, \
    VideoParametersMessage, RemoteMediaStateMessage
from ..packets.v2_7_7.base import LegacySignalingPacketMessage
from ..packets.v2_7_7.remote_media_state import RemoteVideoState, RemoteAudioState
from ..track import PhoneCallIncomingTrack

if TYPE_CHECKING:
    from ..main import PhoneCall

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


class TrackQueue:
    def __init__(self, track: PhoneCallIncomingTrack) -> None:
        self.track = track

    async def put(self, item: Frame) -> None:
        await self.track.on_new_av_packet(item)

class PhoneCallProtocolV2_7_7(PhoneCallProtocol):
    __slots__ = (
        "signaling_seq", "remote_signaling_seq", "transport_seq", "remote_transport_seq", "stop_event", "ping_task",
        "connection", "connection_init", "connection_connected", "recv_task", "rtp_track", "track",
    )

    def __init__(self, client: Client, call: PhoneCall, key: bytes, outgoing: bool) -> None:
        super().__init__(client, call, key, outgoing)

        self.signaling_seq = 0
        self.remote_signaling_seq = 0
        self.transport_seq = 0
        self.remote_transport_seq = 0
        self.stop_event = asyncio.Event()
        self.connection: aioice.Connection | None = None
        self.connection_init = asyncio.Event()
        self.connection_connected = asyncio.Event()
        self.rtp_track = RemoteStreamTrack("audio")
        self.ping_task: asyncio.Task | None = None
        self.recv_task: asyncio.Task | None = None
        self.track: PhoneCallIncomingTrack | None = None

    async def start(self, connections: list[PhoneConnection | PhoneConnectionWebrtc]) -> None:
        turn_servers = []
        stun_servers = []
        for connection in connections:
            if not isinstance(connection, PhoneConnectionWebrtc):
                continue
            if connection.stun:
                stun_servers.append(aioice.StunServer(connection.ip, connection.port))
            if connection.turn:
                turn_servers.append(aioice.TurnServer(
                    connection.ip,
                    connection.port,
                    connection.username,
                    connection.password,
                ))

        self.connection = conn = aioice.Connection(
            ice_controlling=True,
            stun_servers=stun_servers,
            turn_servers=turn_servers,
            transport_policy=aioice.TransportPolicy.RELAY,
        )
        conn._components = {0}
        await conn.gather_candidates()

        await self._send_signaling(
            self.client,
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

        self.connection_init.set()

    async def stop(self) -> None:
        self.stop_event.set()

    async def handle_signaling_update(self, update: UpdatePhoneCallSignalingData) -> None:
        await self.connection_init.wait()

        decrypted, valid = decrypt(update.data, self.key, self.signaling_x(True), ctr=True)
        if not valid:
            print(f"  decrypted (valid={valid}): {decrypted}")
            return

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
                    self.client,
                    LegacySignalingPacket(seq=0, payload=AckMessage(seq=packet.seq)),
                )

            await self._handle_signaling(packet.payload)

    def register_track(self, track: PhoneCallIncomingTrack) -> None:
        self.track = track
        self.rtp_track._queue = TrackQueue(track)

    async def _send_transport(self, packet: LegacySignalingPacket) -> None:
        await self.connection_connected.wait()
        assert self.connection is not None

        packet.seq = self.transport_seq
        self.transport_seq += 1

        print(f"sending (transport): {packet}")

        await self.connection.sendto(
            data=encrypt(packet.write(), self.key, self.transport_x(False), ctr=True),
            component=0,
        )

    async def _send_signaling(self, client: Client, packet: LegacySignalingPacket) -> None:
        packet.seq = self.signaling_seq
        self.signaling_seq += 1

        print(f"sending (signaling): {packet}")

        await client.invoke(SendSignalingData(
            peer=self.call.make_input_call(),
            data=encrypt(packet.write(), self.key, self.signaling_x(False), ctr=True),
        ))

    async def _recv_loop(self) -> None:
        await self.connection_init.wait()
        assert self.connection is not None
        await self.connection.connect()
        self.connection_connected.set()

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
        receiver._track = self.rtp_track
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

        while not self.stop_event.is_set():
            current_time_ms = current_ms()
            try:
                data, component = await self.connection.recvfrom()
            except:
                break
            print(f"Received from component {component}: {data}")
            decrypted, valid = decrypt(data, self.key, self.transport_x(True), ctr=True)
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
                    if self.track is not None and self.track.has_readers():
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

    def _ice_connected_callback(self, _: asyncio.Task) -> None:
        self.recv_task = asyncio.create_task(self._recv_loop())

    async def _handle_signaling(self, payload: LegacySignalingPacketMessage) -> None:
        if isinstance(payload, CandidatesListMessage):
            await self.connection_init.wait()
            assert self.connection is not None

            self.connection.remote_username = payload.ufrag
            self.connection.remote_password = payload.pwd
            if self.recv_task is None:
                self.recv_task = asyncio.create_task(self._recv_loop())

            for candidate_sdp in payload.candidates:
                if not candidate_sdp.startswith("candidate:"):
                    continue
                candidate_sdp = candidate_sdp[10:]
                candidate = aioice.Candidate.from_sdp(candidate_sdp)
                if candidate.host.endswith(".reflector"):
                    # No clue how to work with these
                    continue
                print(f"Adding remote candidate {candidate}")
                await self.connection.add_remote_candidate(candidate)
        elif isinstance(payload, AckMessage):
            print(f"got ack for message {payload.seq}")

