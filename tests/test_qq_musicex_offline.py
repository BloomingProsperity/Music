from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from src.Infrastructure.platforms.qq import musicex_offline
from src.Infrastructure.platforms.qq.adapter import QQPlatformAdapter
from src.Infrastructure.platforms.qq.musicex_offline import (
    MUSICEX_MAGIC,
    QQEncryptedTail,
    parse_encrypted_tail,
)


def _utf16_field(value: str, byte_len: int) -> bytes:
    raw = value.encode("utf-16le")
    if len(raw) > byte_len:
        raise AssertionError("field is too long for test fixture")
    return raw + b"\0" * (byte_len - len(raw))


def _musicex_fixture(song_mid: str = "001xd0HI0X9GNq", filename: str = "F0M0003jT4SU22clE5.mflac") -> bytes:
    payload = bytes((index * 17) & 0xFF for index in range(4096))
    tail = bytearray(192)
    tail[0:4] = (5105986).to_bytes(4, "little")
    tail[4:8] = (2).to_bytes(4, "little")
    tail[8:12] = (5).to_bytes(4, "little")
    tail[12:72] = _utf16_field(song_mid, 60)
    tail[72:168] = _utf16_field(filename, 96)
    tail[176:180] = (192).to_bytes(4, "little")
    tail[180:184] = (1).to_bytes(4, "little")
    tail[184:192] = MUSICEX_MAGIC
    return payload + bytes(tail)


class QQMusicExOfflineTests(unittest.TestCase):
    def test_parse_musicex_tail_returns_song_mid_file_mid_and_audio_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "song.mflac"
            path.write_bytes(_musicex_fixture())

            meta = parse_encrypted_tail(path)

            self.assertEqual(
                meta,
                QQEncryptedTail(
                    format="musicex",
                    song_mid="001xd0HI0X9GNq",
                    filename="F0M0003jT4SU22clE5.mflac",
                    audio_size=4096,
                    ekey=None,
                ),
            )

    def test_parse_qtag_tail_returns_embedded_ekey_and_audio_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "song.mflac"
            payload = b"encrypted-audio"
            ekey_data = b"001songmid,ZmFrZS1la2V5"
            path.write_bytes(payload + ekey_data + len(ekey_data).to_bytes(4, "little") + b"QTag")

            meta = parse_encrypted_tail(path)

            self.assertEqual(meta.format, "legacy")
            self.assertEqual(meta.song_mid, "001songmid")
            self.assertEqual(meta.ekey, "ZmFrZS1la2V5")
            self.assertEqual(meta.audio_size, len(payload))

    def test_tencent_tea_skip_continues_after_crossing_block_boundary(self) -> None:
        decrypted_blocks = iter(
            [
                bytes([7, 0, 0, 0, 0, 0, 0, 0]),
                bytes([10, 11, 12, 13, 14, 15, 16, 17]),
                bytes([20, 21, 22, 23, 24, 25, 26, 27]),
            ]
        )

        with mock.patch.object(musicex_offline, "_tea_decrypt_block", side_effect=lambda _block, _key: next(decrypted_blocks)):
            decoded = musicex_offline._decrypt_tencent_tea(b"\0" * 24, b"\0" * 16)

        self.assertEqual(decoded, bytes([12, 13, 14, 15, 16, 17, 20]))

    def test_short_raw_key_is_rejected_without_crashing(self) -> None:
        self.assertIsNone(musicex_offline.derive_qmc2_key(b"short"))

    def test_short_ekey_is_rejected_without_crashing(self) -> None:
        ekey = base64.b64encode(b"short").decode("ascii")

        self.assertIsNone(musicex_offline.QQOfflineMusicExDecryptor._derive_key_from_ekey(ekey))

    def test_map_cipher_bulk_mask_matches_byte_mask_across_period_boundary(self) -> None:
        cipher = musicex_offline._MapCipher(bytes(range(1, 32)))
        offset = 0x7FFE
        length = 12

        bulk = cipher._mask_bytes(offset, length)
        expected = bytes(cipher._mask(offset + index) for index in range(length))

        self.assertEqual(bulk, expected)

    def test_ekey_request_uses_exact_tail_filename_suffix(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps({"req_1": {"data": {"midurlinfo": [{"ekey": "abc"}]}}}).encode("utf-8")

        def fake_urlopen(request, *, timeout: float):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with mock.patch.object(musicex_offline.urllib.request, "urlopen", side_effect=fake_urlopen):
            ekey = musicex_offline.QQEKeyClient(timeout_seconds=3).fetch(
                "001songmid",
                "C400000000000000.mmp4",
                "qqmusic_key=secret; qqmusic_uin=12345;",
                "12345",
            )

        self.assertEqual(ekey, "abc")
        filename = captured["payload"]["req_1"]["param"]["filename"][0]  # type: ignore[index]
        self.assertEqual(filename, "C400000000000000.mmp4")

    def test_ekey_cache_key_separates_same_song_different_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            decryptor = musicex_offline.QQOfflineMusicExDecryptor(cache_dir=root)
            first = QQEncryptedTail("musicex", "001same", "F0M000first.mflac", 1024, "first-ekey")
            second = QQEncryptedTail("musicex", "001same", "M800second.mgg", 1024, "second-ekey")

            self.assertEqual(decryptor._resolve_ekey(first, {}), "first-ekey")
            self.assertEqual(decryptor._resolve_ekey(second, {}), "second-ekey")

            cached = sorted(path.name for path in root.glob("*.txt"))

        self.assertEqual(len(cached), 2)
        self.assertTrue(any("F0M000first" in name for name in cached))
        self.assertTrue(any("M800second" in name for name in cached))

    def test_fetch_missing_ekey_can_be_disabled_for_cache_only_local_mode(self) -> None:
        class CookieProvider:
            def get_cookie(self):
                raise AssertionError("QQ process cookie should not be read when ekey fetch is disabled")

        with tempfile.TemporaryDirectory() as temp_dir:
            decryptor = musicex_offline.QQOfflineMusicExDecryptor(
                cookie_provider=CookieProvider(),
                cache_dir=pathlib.Path(temp_dir),
            )
            meta = QQEncryptedTail("musicex", "001song", "F0M000song.mflac", 1024)

            self.assertIsNone(decryptor._resolve_ekey(meta, {"qq_fetch_missing_ekey": False}))

    def test_missing_ekey_auto_launches_qqmusic_client_before_fetch(self) -> None:
        class CookieProvider:
            def __init__(self):
                self.calls = 0

            def get_cookie(self):
                self.calls += 1
                if self.calls == 1:
                    return None
                return {"cookie": "uin=o123; qm_keyst=token", "uin": "123"}

        class ClientLauncher:
            def __init__(self):
                self.calls = 0

            def launch(self):
                self.calls += 1
                return True

        class EKeyClient:
            def __init__(self):
                self.calls: list[tuple[str, str, str, str]] = []

            def fetch(self, song_mid, filename, cookie, uin):
                self.calls.append((song_mid, filename, cookie, uin))
                return "fetched-ekey"

        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_provider = CookieProvider()
            client_launcher = ClientLauncher()
            ekey_client = EKeyClient()
            decryptor = musicex_offline.QQOfflineMusicExDecryptor(
                cookie_provider=cookie_provider,
                client_launcher=client_launcher,
                ekey_client=ekey_client,
                cache_dir=pathlib.Path(temp_dir),
                sleep_func=lambda _seconds: None,
            )
            meta = QQEncryptedTail("musicex", "001song", "F0M000song.mflac", 1024)

            ekey = decryptor._resolve_ekey(meta, {"qq_fetch_missing_ekey": True})

        self.assertEqual(ekey, "fetched-ekey")
        self.assertEqual(client_launcher.calls, 1)
        self.assertEqual(cookie_provider.calls, 2)
        self.assertEqual(
            ekey_client.calls,
            [("001song", "F0M000song.mflac", "uin=o123; qm_keyst=token", "123")],
        )

    def test_missing_ekey_auto_launch_can_be_disabled(self) -> None:
        class CookieProvider:
            def get_cookie(self):
                return None

        class ClientLauncher:
            def launch(self):
                raise AssertionError("QQ Music client should not be launched")

        with tempfile.TemporaryDirectory() as temp_dir:
            decryptor = musicex_offline.QQOfflineMusicExDecryptor(
                cookie_provider=CookieProvider(),
                client_launcher=ClientLauncher(),
                cache_dir=pathlib.Path(temp_dir),
                sleep_func=lambda _seconds: None,
            )
            meta = QQEncryptedTail("musicex", "001song", "F0M000song.mflac", 1024)

            ekey = decryptor._resolve_ekey(
                meta,
                {"qq_fetch_missing_ekey": True, "qq_auto_launch_client": False},
            )

        self.assertIsNone(ekey)

    def test_missing_ekey_auto_launch_is_attempted_once_when_cookie_stays_missing(self) -> None:
        class CookieProvider:
            def get_cookie(self):
                return None

        class ClientLauncher:
            def __init__(self):
                self.calls = 0

            def launch(self):
                self.calls += 1
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            client_launcher = ClientLauncher()
            decryptor = musicex_offline.QQOfflineMusicExDecryptor(
                cookie_provider=CookieProvider(),
                client_launcher=client_launcher,
                cache_dir=pathlib.Path(temp_dir),
                sleep_func=lambda _seconds: None,
            )
            first = QQEncryptedTail("musicex", "001song", "F0M000song.mflac", 1024)
            second = QQEncryptedTail("musicex", "002song", "F0M000other.mflac", 1024)

            self.assertIsNone(decryptor._resolve_ekey(first, {"qq_client_launch_wait_seconds": 0}))
            self.assertIsNone(decryptor._resolve_ekey(second, {"qq_client_launch_wait_seconds": 0}))

        self.assertEqual(client_launcher.calls, 1)

    def test_client_launcher_uses_running_process_path_before_common_install_dirs(self) -> None:
        class Match:
            exe_path = r"D:\PortableApps\QQMusic\QQMusic.exe"

        with (
            mock.patch.object(musicex_offline, "find_process_by_name", return_value=Match()),
            mock.patch.object(musicex_offline.shutil, "which", return_value=None),
            mock.patch.object(musicex_offline.pathlib.Path, "exists", return_value=True),
        ):
            executable = musicex_offline.QQMusicClientLauncher._find_executable()

        self.assertEqual(executable, pathlib.Path(r"D:\PortableApps\QQMusic\QQMusic.exe"))

    def test_client_launcher_only_reports_install_required_when_client_is_missing(self) -> None:
        calls: list[list[str]] = []

        with (
            mock.patch.object(musicex_offline.QQMusicClientLauncher, "_find_executable", return_value=None),
            mock.patch.object(musicex_offline.shutil, "which", return_value=r"C:\Windows\System32\winget.exe"),
            mock.patch.object(musicex_offline.subprocess, "Popen", side_effect=lambda command, **_kwargs: calls.append(list(command))),
        ):
            launcher = musicex_offline.QQMusicClientLauncher()
            launched = launcher.launch()

        self.assertFalse(launched)
        self.assertEqual(launcher.last_action, "install_required")
        self.assertEqual(calls, [])

    def test_relative_ekey_cache_dir_override_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_cache = pathlib.Path(temp_dir) / "default-cache"
            decryptor = musicex_offline.QQOfflineMusicExDecryptor(cache_dir=default_cache)

            with self.assertLogs("qkkdecrypt.infrastructure.platforms.qq.musicex_offline", level="WARNING"):
                path = decryptor._cache_path("cache-key", {"qq_ekey_cache_dir": "relative-cache"})

        self.assertEqual(path.parent, default_cache)

    def test_native_loader_does_not_compile_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            (root / "qmc2_fast.c").write_text("int x;", encoding="utf-8")
            old_lib = musicex_offline._QMC2_NATIVE_LIB
            old_attempted = musicex_offline._QMC2_NATIVE_ATTEMPTED
            musicex_offline._QMC2_NATIVE_LIB = None
            musicex_offline._QMC2_NATIVE_ATTEMPTED = False
            try:
                with (
                    mock.patch.dict("os.environ", {}, clear=True),
                    mock.patch.object(musicex_offline, "_qmc2_native_dir", return_value=root),
                    mock.patch.object(musicex_offline.ctypes, "CDLL") as cdll,
                ):
                    self.assertIsNone(musicex_offline._load_qmc2_native())
            finally:
                musicex_offline._QMC2_NATIVE_LIB = old_lib
                musicex_offline._QMC2_NATIVE_ATTEMPTED = old_attempted

            cdll.assert_not_called()

    def test_native_loader_requires_matching_signature_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            (root / "qmc2_fast.dll").write_bytes(b"fake-dll")
            old_lib = musicex_offline._QMC2_NATIVE_LIB
            old_attempted = musicex_offline._QMC2_NATIVE_ATTEMPTED
            musicex_offline._QMC2_NATIVE_LIB = None
            musicex_offline._QMC2_NATIVE_ATTEMPTED = False
            try:
                with (
                    mock.patch.dict("os.environ", {"QKK_QQ_QMC2_NATIVE": "1"}, clear=True),
                    mock.patch.object(musicex_offline, "_qmc2_native_dir", return_value=root),
                    mock.patch.object(musicex_offline, "_qmc2_native_name", return_value="qmc2_fast.dll"),
                    mock.patch.object(musicex_offline.ctypes, "CDLL") as cdll,
                ):
                    self.assertIsNone(musicex_offline._load_qmc2_native())
            finally:
                musicex_offline._QMC2_NATIVE_LIB = old_lib
                musicex_offline._QMC2_NATIVE_ATTEMPTED = old_attempted

            cdll.assert_not_called()

    def test_signed_native_loads_by_default_when_packaged(self) -> None:
        class FakeFunction:
            def __init__(self) -> None:
                self.argtypes = None
                self.restype = None

        class FakeLibrary:
            def __init__(self) -> None:
                self.qmc2_decrypt = FakeFunction()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            dll = root / "qmc2_fast.dll"
            dll.write_bytes(b"fake-dll")
            (root / "qmc2_fast.dll.sha256").write_text(hashlib.sha256(dll.read_bytes()).hexdigest(), encoding="utf-8")
            fake_library = FakeLibrary()
            old_lib = musicex_offline._QMC2_NATIVE_LIB
            old_attempted = musicex_offline._QMC2_NATIVE_ATTEMPTED
            musicex_offline._QMC2_NATIVE_LIB = None
            musicex_offline._QMC2_NATIVE_ATTEMPTED = False
            try:
                with (
                    mock.patch.dict("os.environ", {}, clear=True),
                    mock.patch.object(musicex_offline.sys, "frozen", True, create=True),
                    mock.patch.object(musicex_offline, "_qmc2_native_dir", return_value=root),
                    mock.patch.object(musicex_offline, "_qmc2_native_name", return_value="qmc2_fast.dll"),
                    mock.patch.object(musicex_offline.ctypes, "CDLL", return_value=fake_library) as cdll,
                ):
                    self.assertIs(musicex_offline._load_qmc2_native(), fake_library)
            finally:
                musicex_offline._QMC2_NATIVE_LIB = old_lib
                musicex_offline._QMC2_NATIVE_ATTEMPTED = old_attempted

            cdll.assert_called_once()

    def test_signed_native_loads_by_default_when_available_in_source_tree(self) -> None:
        class FakeFunction:
            def __init__(self) -> None:
                self.argtypes = None
                self.restype = None

        class FakeLibrary:
            def __init__(self) -> None:
                self.qmc2_decrypt = FakeFunction()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            dll = root / "qmc2_fast.dll"
            dll.write_bytes(b"fake-dll")
            (root / "qmc2_fast.dll.sha256").write_text(hashlib.sha256(dll.read_bytes()).hexdigest(), encoding="utf-8")
            fake_library = FakeLibrary()
            old_lib = musicex_offline._QMC2_NATIVE_LIB
            old_attempted = musicex_offline._QMC2_NATIVE_ATTEMPTED
            musicex_offline._QMC2_NATIVE_LIB = None
            musicex_offline._QMC2_NATIVE_ATTEMPTED = False
            try:
                with (
                    mock.patch.dict("os.environ", {}, clear=True),
                    mock.patch.object(musicex_offline.sys, "frozen", False, create=True),
                    mock.patch.object(musicex_offline, "_qmc2_native_dir", return_value=root),
                    mock.patch.object(musicex_offline, "_qmc2_native_name", return_value="qmc2_fast.dll"),
                    mock.patch.object(musicex_offline.ctypes, "CDLL", return_value=fake_library) as cdll,
                ):
                    self.assertIs(musicex_offline._load_qmc2_native(), fake_library)
            finally:
                musicex_offline._QMC2_NATIVE_LIB = old_lib
                musicex_offline._QMC2_NATIVE_ATTEMPTED = old_attempted

            cdll.assert_called_once()

    def test_payload_decrypt_uses_fast_native_path_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.mflac"
            output = root / "song.flac"
            source.write_bytes(b"encrypted")

            def fake_fast(_key: bytes, buffer: bytearray, _offset: int) -> bool:
                buffer[:] = b"decrypted"
                return True

            with (
                mock.patch.object(musicex_offline, "decrypt_qmc2_buffer_fast", side_effect=fake_fast, create=True) as fast,
                mock.patch.object(musicex_offline, "_make_cipher") as make_cipher,
            ):
                musicex_offline.QQOfflineMusicExDecryptor._decrypt_payload(source, output, 9, b"key")

            fast.assert_called_once()
            make_cipher.assert_not_called()
            self.assertEqual(output.read_bytes(), b"decrypted")

    def test_native_binding_uses_64_bit_stream_offset(self) -> None:
        class FakeFunction:
            def __init__(self) -> None:
                self.argtypes = None
                self.restype = None

        class FakeLibrary:
            def __init__(self) -> None:
                self.qmc2_decrypt = FakeFunction()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            dll = root / "qmc2_fast.dll"
            dll.write_bytes(b"fake")
            (root / "qmc2_fast.dll.sha256").write_text(hashlib.sha256(dll.read_bytes()).hexdigest(), encoding="utf-8")
            fake_library = FakeLibrary()
            old_lib = musicex_offline._QMC2_NATIVE_LIB
            old_attempted = musicex_offline._QMC2_NATIVE_ATTEMPTED
            musicex_offline._QMC2_NATIVE_LIB = None
            musicex_offline._QMC2_NATIVE_ATTEMPTED = False
            try:
                with (
                    mock.patch.dict("os.environ", {"QKK_QQ_QMC2_NATIVE": "1"}, clear=True),
                    mock.patch.object(musicex_offline, "_qmc2_native_dir", return_value=root),
                    mock.patch.object(musicex_offline, "_qmc2_native_name", return_value="qmc2_fast.dll"),
                    mock.patch.object(musicex_offline.ctypes, "CDLL", return_value=fake_library),
                ):
                    musicex_offline._load_qmc2_native()
            finally:
                musicex_offline._QMC2_NATIVE_LIB = old_lib
                musicex_offline._QMC2_NATIVE_ATTEMPTED = old_attempted

            self.assertEqual(fake_library.qmc2_decrypt.argtypes[-1], musicex_offline.ctypes.c_longlong)

    def test_native_binding_returns_status_and_uses_size_t_lengths(self) -> None:
        class FakeFunction:
            def __init__(self) -> None:
                self.argtypes = None
                self.restype = None

        class FakeLibrary:
            def __init__(self) -> None:
                self.qmc2_decrypt = FakeFunction()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            dll = root / "qmc2_fast.dll"
            dll.write_bytes(b"fake")
            digest = hashlib.sha256(dll.read_bytes()).hexdigest()
            (root / "qmc2_fast.dll.sha256").write_text(digest + "\n", encoding="utf-8")
            fake_library = FakeLibrary()
            old_lib = musicex_offline._QMC2_NATIVE_LIB
            old_attempted = musicex_offline._QMC2_NATIVE_ATTEMPTED
            musicex_offline._QMC2_NATIVE_LIB = None
            musicex_offline._QMC2_NATIVE_ATTEMPTED = False
            try:
                with (
                    mock.patch.dict("os.environ", {"QKK_QQ_QMC2_NATIVE": "1"}, clear=True),
                    mock.patch.object(musicex_offline, "_qmc2_native_dir", return_value=root),
                    mock.patch.object(musicex_offline, "_qmc2_native_name", return_value="qmc2_fast.dll"),
                    mock.patch.object(musicex_offline.ctypes, "CDLL", return_value=fake_library),
                ):
                    musicex_offline._load_qmc2_native()
            finally:
                musicex_offline._QMC2_NATIVE_LIB = old_lib
                musicex_offline._QMC2_NATIVE_ATTEMPTED = old_attempted

            self.assertEqual(fake_library.qmc2_decrypt.argtypes[1], musicex_offline.ctypes.c_size_t)
            self.assertEqual(fake_library.qmc2_decrypt.argtypes[3], musicex_offline.ctypes.c_size_t)
            self.assertEqual(fake_library.qmc2_decrypt.restype, musicex_offline.ctypes.c_int)

    def test_windows_process_api_prototypes_are_configured(self) -> None:
        class FakeFunction:
            argtypes = None
            restype = None

        class FakeKernel32:
            OpenProcess = FakeFunction()
            CloseHandle = FakeFunction()
            VirtualQueryEx = FakeFunction()
            ReadProcessMemory = FakeFunction()

        class FakePsapi:
            EnumProcesses = FakeFunction()
            GetModuleBaseNameA = FakeFunction()

        class FakeMemoryInfo(musicex_offline.ctypes.Structure):
            _fields_ = [("dummy", musicex_offline.ctypes.c_int)]

        musicex_offline.configure_windows_process_api(FakeKernel32, FakePsapi, FakeMemoryInfo)

        self.assertEqual(FakeKernel32.OpenProcess.restype, musicex_offline.ctypes.wintypes.HANDLE)
        self.assertEqual(FakeKernel32.ReadProcessMemory.restype, musicex_offline.ctypes.wintypes.BOOL)
        self.assertEqual(FakePsapi.EnumProcesses.restype, musicex_offline.ctypes.wintypes.BOOL)

    def test_cookie_provider_uses_process_query_to_find_qqmusic_pid(self) -> None:
        class Match:
            pid = 23156

        with mock.patch.object(musicex_offline, "find_process_by_name", return_value=Match()):
            pid = musicex_offline.QQMusicCookieProvider._find_qqmusic_pid(None, None)

        self.assertEqual(pid, 23156)

    def test_adapter_uses_offline_musicex_decrypt(self) -> None:
        class OfflineDecryptor:
            def __init__(self) -> None:
                self.calls: list[pathlib.Path] = []

            def decrypt_to_file(
                self,
                input_path: pathlib.Path,
                output_path: pathlib.Path,
                settings: dict,
                *,
                log_dir: pathlib.Path,
            ) -> dict | None:
                self.calls.append(input_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fLaC" + b"\0" * 4096)
                return {
                    "backend": "qmc2:musicex",
                    "detected_container": "flac",
                    "recognition_stage": "offline",
                    "artifact_state": "decoded",
                    "decoded_bytes": output_path.stat().st_size,
                }

        class TestAdapter(QQPlatformAdapter):
            def __init__(self, offline: OfflineDecryptor) -> None:
                super().__init__()
                self.offline = offline

            def _ensure_offline_decryptor(self):
                return self.offline

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.mflac"
            work_dir = root / "work"
            source.write_bytes(_musicex_fixture())
            offline = OfflineDecryptor()
            adapter = TestAdapter(offline)

            detail = adapter.decrypt_one(source, work_dir, {}, log_dir=root)

            self.assertEqual(offline.calls, [source])
            self.assertEqual(detail["backend"], "qmc2:musicex")
            self.assertEqual(detail["detected_container"], "flac")
            self.assertEqual(pathlib.Path(detail["output_path"]).read_bytes()[:4], b"fLaC")

    def test_adapter_defaults_to_local_mode_without_qqmusic_process(self) -> None:
        adapter = QQPlatformAdapter()

        self.assertFalse(adapter.requires_running_process())
        self.assertEqual(adapter.validate_runtime({}), (True, None))

    def test_adapter_raises_when_local_key_is_missing(self) -> None:
        class OfflineDecryptor:
            def decrypt_to_file(
                self,
                _input_path: pathlib.Path,
                _output_path: pathlib.Path,
                _settings: dict,
                *,
                log_dir: pathlib.Path,
            ) -> dict | None:
                return None

        class TestAdapter(QQPlatformAdapter):
            def _ensure_offline_decryptor(self):
                return OfflineDecryptor()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.mflac"
            source.write_bytes(_musicex_fixture())
            adapter = TestAdapter()

            with self.assertRaisesRegex(RuntimeError, "qq_local_ekey_missing"):
                adapter.decrypt_one(source, root / "work", {}, log_dir=root)


if __name__ == "__main__":
    unittest.main()
