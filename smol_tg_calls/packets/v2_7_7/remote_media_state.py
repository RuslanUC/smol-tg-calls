from __future__ import annotations

from enum import IntEnum
from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.utils import uint_be_from_bytes, u8be_to_bytes


class RemoteVideoState(IntEnum):
    INACTIVE = 0
    PAUSED = 1
    ACTIVE = 2


class RemoteAudioState(IntEnum):
    MUTED = 0
    ACTIVE = 1


class RemoteMediaStateMessage(LegacySignalingPacketMessage):
    PACKET_TYPE = 4
    REQUIRES_ACK = True

    __slots__ = ("video_state", "audio_state",)

    def __init__(self, video_state: RemoteVideoState, audio_state: RemoteAudioState) -> None:
        self.video_state = video_state
        self.audio_state = audio_state

    @classmethod
    def read(cls, data: BytesIO) -> RemoteMediaStateMessage:
        state = uint_be_from_bytes(data.read(1))
        audio = RemoteAudioState(state & 0b01)
        video = RemoteVideoState((state >> 1) & 0b11)
        return RemoteMediaStateMessage(
            video_state=video,
            audio_state=audio,
        )

    def write(self) -> bytes:
        return u8be_to_bytes((self.video_state.value << 1) | self.audio_state.value)
