from __future__ import annotations

from src.Infrastructure import native_backend
from src.Infrastructure.native_backend import NativeKudogBackend


def test_const_buffer_cache_does_not_reuse_different_values_with_same_object_id(monkeypatch) -> None:
    backend = NativeKudogBackend(None)
    monkeypatch.setattr(native_backend, "id", lambda _value: 12345, raising=False)

    first = backend._cached_const_buffer(b"first-key", "rc4")
    second = backend._cached_const_buffer(b"second-key", "rc4")

    assert bytes(first) == b"first-key"
    assert bytes(second) == b"second-key"
