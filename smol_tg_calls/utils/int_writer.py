def u8le_to_bytes(num: int) -> bytes:
    return num.to_bytes(1, "little", signed=False)


def u16le_to_bytes(num: int) -> bytes:
    return num.to_bytes(2, "little", signed=False)


def u32le_to_bytes(num: int) -> bytes:
    return num.to_bytes(4, "little", signed=False)


def uint_le_from_bytes(data: bytes) -> int:
    return int.from_bytes(data, "little", signed=False)


def uint_be_from_bytes(data: bytes) -> int:
    return int.from_bytes(data, "big", signed=False)


def u8be_to_bytes(num: int) -> bytes:
    return num.to_bytes(1, "big", signed=False)


def u16be_to_bytes(num: int) -> bytes:
    return num.to_bytes(2, "big", signed=False)


def u32be_to_bytes(num: int) -> bytes:
    return num.to_bytes(4, "big", signed=False)
