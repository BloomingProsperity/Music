from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from src.Infrastructure import kugou_decoder
from src.Infrastructure.kugou_decoder import KugouHeader, UnrecognizedAudioContainerError, decode_file


def test_decode_file_rejects_unrecognized_payload_by_default(tmp_path: pathlib.Path, monkeypatch) -> None:
    source = tmp_path / "song.kgm"
    source.write_bytes(b"encrypted")
    header = KugouHeader(
        magic_header=kugou_decoder.KGM_MAGIC,
        audio_offset=0,
        crypto_version=3,
        crypto_slot=0,
        crypto_test_data=b"\0" * 16,
        crypto_key=b"",
    )

    def fake_decode_v3_stream(src, dst, own_key, pub_key, **kwargs) -> dict:
        dst.write(b"not an audio container")
        return {"decoded_bytes": 22, "detected_container": "bin"}

    monkeypatch.setattr(kugou_decoder, "parse_header_file", lambda _path: header)
    monkeypatch.setattr(kugou_decoder, "load_public_key", lambda _path: b"public-key")
    monkeypatch.setattr(kugou_decoder, "_decode_v3_stream", fake_decode_v3_stream)
    monkeypatch.setattr(kugou_decoder, "probe_audio_container", lambda _path: None)
    monkeypatch.setattr(kugou_decoder, "get_native_backend", lambda: SimpleNamespace(available=False, dll_path=None))

    with pytest.raises(UnrecognizedAudioContainerError) as exc_info:
        decode_file(source, tmp_path / "out", key_path=tmp_path / "kugou_key.xz")

    assert exc_info.value.summary["detected_container"] == "bin"
    assert exc_info.value.summary["output_path"] is None
    assert not list((tmp_path / "out").glob("*"))
