from snapgit.ingest.dedup import sha256_bytes


def test_sha256_bytes_matches_known_vector_for_abc():
    assert (
        sha256_bytes(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_bytes_is_deterministic():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")


def test_sha256_bytes_differs_for_distinct_inputs():
    assert sha256_bytes(b"abc") != sha256_bytes(b"abcd")
