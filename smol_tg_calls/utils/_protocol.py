from pyrogram.raw.types import PhoneCallProtocol


def _make_protocol(version: str | list[str]) -> PhoneCallProtocol:
    return PhoneCallProtocol(
        min_layer=65,
        max_layer=92,
        library_versions=version if isinstance(version, list) else [version],
        udp_p2p=False,
        udp_reflector=True,
    )
