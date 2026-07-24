from __future__ import annotations

from abc import ABC

from smol_tg_calls.packets import PacketPayloadBase


class LegacySignalingPacketMessage(PacketPayloadBase, ABC):
    REQUIRES_ACK: bool

    __slots__ = ()
