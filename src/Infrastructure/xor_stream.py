from __future__ import annotations

try:
    import numpy as _np
except Exception:  # pragma: no cover - depends on runtime environment
    _np = None  # type: ignore[assignment]


def _xor_repeating_key_python(data: bytearray, key: bytes, start_offset: int) -> None:
    key_len = len(key)
    for index in range(len(data)):
        data[index] ^= key[(start_offset + index) % key_len]


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
    key_view = _np.frombuffer(key, dtype=_np.uint8)
    positions = (_np.arange(view.size, dtype=_np.uint64) + normalized_offset) % key_view.size
    _np.bitwise_xor(view, key_view[positions], out=view)
    return "numpy"
