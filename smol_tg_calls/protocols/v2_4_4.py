from __future__ import annotations

import asyncio
import queue
from fractions import Fraction
from io import BytesIO
from threading import Thread
from typing import TYPE_CHECKING, cast

from aiortc.codecs import Decoder
from aiortc.jitterbuffer import JitterBuffer, JitterFrame
from aiortc.rtp import RtpPacket
from av import CodecContext
from av.frame import Frame
from av.packet import Packet as AvPacket
from pyrogram import Client
from pyrogram.raw.types import PhoneConnection, UpdatePhoneCallSignalingData, PhoneConnectionWebrtc

from .base import PhoneCallProtocol
from ..aioudp import open_remote_endpoint
from ..crypto import decrypt, encrypt
from ..packets.v2_4_4 import Packet, PacketHeader, PacketInit, PacketInitAck, PacketPing, PacketPong, PacketStreamData, \
    Stream
from ..in_track import PhoneCallIncomingTrack
from ..udp_endpoint import UdpEndpoint
from ..utils import coro_with_additional_return

if TYPE_CHECKING:
    from ..main import PhoneCall

OPUS = int.from_bytes(b"SUPO", "little", signed=False)


class OpusDecoder(Decoder):
    def __init__(self, mspf: int) -> None:
        self.mspf = mspf
        self.codec = CodecContext.create("opus", "r")
        self.codec.format = "s16"
        self.codec.layout = "mono"
        self.codec.sample_rate = 48000

    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        packet = AvPacket(encoded_frame.data)
        packet.pts = encoded_frame.timestamp
        packet.time_base = Fraction(1, 48000)
        return cast(list[Frame], self.codec.decode(packet))


class PhoneCallProtocolV2_4_4(PhoneCallProtocol):
    __slots__ = (
        "seq", "remote_seq", "endpoints", "stop_event", "ping_task", "jitter_buffer", "worker_thread", "worker_queue",
        "track",
    )

    def __init__(self, client: Client, call: PhoneCall, key: bytes, outgoing: bool) -> None:
        super().__init__(client, call, key, outgoing)

        self.seq = 0
        self.remote_seq = 0
        self.endpoints: list[UdpEndpoint] = []
        self.stop_event = asyncio.Event()
        self.ping_task: asyncio.Task | None = None
        self.worker_queue: queue.Queue[JitterFrame | None] = queue.Queue()
        self.jitter_buffer = JitterBuffer(capacity=16, prefetch=4)
        self.worker_thread: Thread | None = None
        self.track: PhoneCallIncomingTrack | None = None

    def _decoder_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        decoder: OpusDecoder | None = None

        while True:
            encoded_frame = self.worker_queue.get()
            if encoded_frame is None:
                break
            if self.track is None or not self.track.has_readers():
                continue

            if decoder is None:
                # Assuming frame duration is always 60ms and never changes
                decoder = OpusDecoder(60)

            for frame in decoder.decode(encoded_frame):
                if self.track is None or not self.track.has_readers():
                    continue
                asyncio.run_coroutine_threadsafe(self.track.on_new_av_packet(frame), loop)

    async def start(self, connections: list[PhoneConnection | PhoneConnectionWebrtc]) -> None:
        self.worker_thread = Thread(target=self._decoder_worker, args=(asyncio.get_running_loop(),))
        self.worker_thread.start()

        for connection in connections:
            if not isinstance(connection, PhoneConnection) or connection.tcp:
                continue
            self.endpoints.append(UdpEndpoint(
                await open_remote_endpoint(connection.ip, connection.port),
                connection.peer_tag,
            ))

        if not self.endpoints:
            raise ValueError("No available connections found")

        await self._req_reflector_peer_self_info()
        self._send_init()

        done, pending = await asyncio.wait([
            asyncio.create_task(coro_with_additional_return(endpoint.receive(), endpoint))
            for endpoint in self.endpoints
        ], return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            if not task.done():
                task.cancel()

        data, endpoint = await list(done)[0]
        await self._process_packet(data)

        self.ping_task = asyncio.create_task(self._ping_loop())

        while not self.stop_event.is_set():
            data = await endpoint.receive()
            await self._process_packet(data)

        for endpoint in self.endpoints:
            endpoint.close()

        self.worker_queue.put_nowait(None)

    async def stop(self) -> None:
        self.stop_event.set()
        self.worker_queue.put_nowait(None)

    async def handle_signaling_update(self, update: UpdatePhoneCallSignalingData) -> None:
        pass

    def register_track(self, track: PhoneCallIncomingTrack) -> None:
        self.track = track

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
                audio_streams=[OPUS],
                video_streams=[],
            )
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, self.transport_x(False)))

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
                        codec=OPUS,
                        frame_duration=60,
                        enabled=1,
                    )
                ],
            )
        )
        print(f"sending: {packet_to_send}")
        to_send = packet_to_send.write(True)
        self._send_to_all(encrypt(to_send, self.key, self.transport_x(False)))

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
        self._send_to_all(encrypt(to_send, self.key, self.transport_x(False)))

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
        self._send_to_all(encrypt(to_send, self.key, self.transport_x(False)))

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
        self._send_to_all(encrypt(to_send, self.key, self.transport_x(False)))

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

        decrypted, valid = decrypt(data, self.key, self.transport_x(True))
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
            # for stream in payload.streams:
            #     if stream.type == OPUS:
            #         self.opus_decoder.mspf = stream.frame_duration
            self._send_init()
        elif isinstance(payload, PacketPing):
            self._send_pong()
        elif isinstance(payload, PacketStreamData):
            # self.worker_queue.put_nowait(JitterFrame(payload.data, payload.pts))

            # This probably makes more sense, idk?
            rtp_packet = RtpPacket(
                payload_type=111,
                # Assuming frame duration is always 60ms and never changes
                sequence_number=payload.pts // 60,
                timestamp=payload.pts,
                ssrc=payload.stream_id,
                payload=payload.data,
            )
            rtp_packet._data = payload.data
            _, frame = self.jitter_buffer.add(rtp_packet)
            if frame is not None:
                self.worker_queue.put_nowait(frame)

            # if self.sending_audio is None:
            #     if self.opus_frames is None:
            #         print("Loading opus frames...")
            #         self.opus_frames = await asyncio.to_thread(_load_opus, "audio-mono.wav", 60)
            #     self.sending_audio = 0
            #     asyncio.get_running_loop().call_later(0.060, self._send_audio_frame)
            ...
            # self._send_stream_data(payload)

    async def _ping_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), 0.5)
            except TimeoutError:
                self._send_ping()
            else:
                break
