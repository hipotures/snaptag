from snapgit.ingest.dedup import sha256_bytes


def test_sha256_bytes_is_deterministic():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
