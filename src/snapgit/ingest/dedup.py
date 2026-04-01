from hashlib import sha256


def sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()
