from __future__ import annotations

from io import BytesIO

from smol_tg_calls.packets.v2_7_7.base import LegacySignalingPacketMessage
from smol_tg_calls.packets.v2_7_7.string_value import StringValue
from smol_tg_calls.utils import uint_be_from_bytes, u8be_to_bytes


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
