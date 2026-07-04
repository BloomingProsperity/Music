from __future__ import annotations

try:
    import numpy as _np
except Exception:  # pragma: no cover - depends on runtime environment
    _np = None  # type: ignore[assignment]


def _xor_repeating_key_python(data: bytearray, key: bytes, start_offset: int) -> None:
    key_len = len(key)
    for index in range(len(data)):
        data[index] ^= key[(start_offset + index) % key_len]


def _repeating_key_mask(key: bytes, length: int, start_offset: int) -> bytes:
    if length <= 0:
        return b""
    key_len = len(key)
    offset = start_offset % key_len
    rotated = key[offset:] + key[:offset]
    repeat_count = (length + key_len - 1) // key_len
    return (rotated * repeat_count)[:length]


def xor_repeating_key_inplace(data: bytearray, key: bytes, start_offset: int = 0) -> str:
    if not key:
        raise ValueError("xor key must not be empty")
    if not data:
        return "none"
    normalized_offset = int(start_offset or 0)
    if _np is None:
        _xor_repeating_key_python(data, key, normalized_offset)
        return "python"

    view = _np.frombuffer(data, dtype=_np.uint8)
    mask = _np.frombuffer(_repeating_key_mask(key, view.size, normalized_offset), dtype=_np.uint8)
    _np.bitwise_xor(view, mask, out=view)
    return "numpy"
