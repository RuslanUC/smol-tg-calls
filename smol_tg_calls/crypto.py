from hashlib import sha256

import tgcrypto


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
