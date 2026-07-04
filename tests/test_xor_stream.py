from __future__ import annotations

from src.Infrastructure import xor_stream


def _reference(data: bytes, key: bytes, start_offset: int) -> bytes:
    return bytes(byte ^ key[(start_offset + index) % len(key)] for index, byte in enumerate(data))


def test_xor_repeating_key_inplace_matches_reference_for_offsets() -> None:
    key = bytes(range(1, 33))
    source = bytes((index * 19 + 5) & 0xFF for index in range(1027))

    for offset in (0, 1, 17, 31, 32, 255, 4097):
        block = bytearray(source)

        xor_stream.xor_repeating_key_inplace(block, key, offset)

        assert bytes(block) == _reference(source, key, offset)


def test_repeating_key_mask_rotates_from_start_offset() -> None:
    key = b"abcdef"

    assert xor_stream._repeating_key_mask(key, 14, 4) == b"efabcdefabcdef"


def test_xor_repeating_key_inplace_keeps_python_fallback_correct(monkeypatch) -> None:
    key = bytes(range(255, 0, -1))
    source = bytes((index * 7 + 11) & 0xFF for index in range(513))
    block = bytearray(source)
    monkeypatch.setattr(xor_stream, "_np", None)

    backend = xor_stream.xor_repeating_key_inplace(block, key, 123)

    assert backend == "python"
    assert bytes(block) == _reference(source, key, 123)
