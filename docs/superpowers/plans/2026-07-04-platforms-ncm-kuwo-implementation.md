# Platforms NCM/Kugou/Kuwo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the current UI to run existing Netease and Kugou backends, add a tested Kuwo `.kwm` local decoder prototype, and verify each supported path without falsely claiming sample success where samples are absent.

**Architecture:** Keep `Application.decrypt_service` as the shared batch pipeline. Add platform-specific settings in `Presentation.ui_state`, keep runtime preflight in UI before starting a worker thread, and add a Kuwo adapter that implements the existing `PlatformAdapter` protocol.

**Tech Stack:** Python 3.12, PySide6, pytest, ffmpeg/ffprobe through existing `transcoder.py`, ncmdump-py for Netease, existing Kugou decoder, new pure-Python Kuwo XOR decoder.

---

### Task 1: Generic UI Batch Config

**Files:**
- Modify: `src/Presentation/ui_state.py`
- Test: `tests/test_ui_state.py`

- [ ] **Step 1: Write failing tests**

Add tests that call `build_platform_batch_config("netease", options)` and `build_platform_batch_config("kugou", options)`. Assert that platform id, input/output paths, recursive flag, transcode settings, bitrate/sample rate, stop/event callbacks, and format values are preserved.

- [ ] **Step 2: Run targeted test**

Run: `python -m pytest tests/test_ui_state.py -q`

Expected before implementation: import or name failure for `build_platform_batch_config`.

- [ ] **Step 3: Implement builder**

Add `build_platform_batch_config(platform_id: str, options: PlatformRunOptions) -> BatchRunConfig`.

Rules:
- For `qq`, delegate to `build_qq_batch_config`.
- For `netease`, include `target_format_ncm`.
- For `kugou`, include `target_format_kgma`, `target_format_kgg`, `key_file`, and `kgg_db_path` if present in `options.platform_settings`.
- For all platforms, include shared transcode, cover, album metadata, bitrate, sample rate, event sink, and stop callback.

- [ ] **Step 4: Run targeted test**

Run: `python -m pytest tests/test_ui_state.py -q`

Expected: pass.

### Task 2: UI Runtime Preflight

**Files:**
- Modify: `src/Presentation/ui_state.py`
- Modify: `src/Presentation/ui_app.py`
- Test: `tests/test_ui_state.py`

- [ ] **Step 1: Write tests for preflight helper**

Add tests for `validate_platform_runtime_for_ui`.

Cases:
- Netease with existing input and writable output returns ok.
- Kugou with missing `kugou_key.xz` returns a clear error.
- Kugou with `.kgg` candidate and no `KGMusicV3.db` returns a clear error.
- Kuwo returns experimental/not available until the adapter task lands.

- [ ] **Step 2: Implement helper**

Create helper in `ui_state.py` that accepts platform id, adapter, settings, input path, output path, and recursive flag. It calls `validate_writable_output_dir`, adapter `validate_runtime`, and for Kugou checks collected `.kgg` files against `kgg_db_path`.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_ui_state.py -q`

Expected: pass.

### Task 3: Enable Netease and Kugou in UI

**Files:**
- Modify: `src/Presentation/ui_state.py`
- Modify: `src/Presentation/ui_app.py`
- Test: `tests/test_ui_layout.py`
- Test: `tests/test_ui_state.py`

- [ ] **Step 1: Update UI specs**

Set `netease.enabled=True`, `kugou.enabled=True`, both status text to available. Keep `kuwo.enabled=False` with a research/experimental status.

- [ ] **Step 2: Generalize save/load**

Replace `_save_qq_config` with `_save_platform_config` that persists common shared settings and platform format values. Keep QQ-specific ekey fields only for QQ.

- [ ] **Step 3: Generalize runner**

Replace `_run_qq` with `_run_platform(platform_id, options)`. `_start_platform` should no longer reject Netease or Kugou; it should reject Kuwo until the Kuwo adapter is implemented and validated.

- [ ] **Step 4: Run UI tests**

Run: `python -m pytest tests/test_ui_layout.py tests/test_ui_state.py -q`

Expected: pass.

### Task 4: Kuwo Decoder Unit

**Files:**
- Create: `src/Infrastructure/kuwo_decoder.py`
- Test: `tests/test_kuwo_decoder.py`

- [ ] **Step 1: Write synthetic decoder test**

Create a minimal fake KWM:
- 1024-byte header.
- Two repeated 32-byte key blocks after the header so key discovery succeeds.
- Body is known audio-like bytes XORed by `key[i & 31]`.

Assert `decode_kwm_file(input, output_dir)` writes the original body bytes and reports `detected_container` from fast/ffmpeg probing.

- [ ] **Step 2: Implement decoder**

Implement:
- `KwmDecodeError`.
- `find_kwm_key(fp)`.
- `decode_kwm_file(input_path, output_dir)`.

The decoder must stream in chunks and XOR with the 32-byte key; it must not read the whole file into memory.

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/test_kuwo_decoder.py -q`

Expected: pass.

### Task 5: Kuwo Adapter and CLI

**Files:**
- Modify: `src/Infrastructure/platforms/registry.py`
- Create: `src/Infrastructure/platforms/kuwo/__init__.py`
- Create: `src/Infrastructure/platforms/kuwo/adapter.py`
- Modify: `src/Infrastructure/config_repository.py`
- Modify: `src/Presentation/cli.py`
- Test: `tests/test_cli_parser.py`
- Test: `tests/test_config_repository.py`

- [ ] **Step 1: Write parser/config tests**

Assert:
- `load_config` contains `kuwo`.
- `python main.py kuwo decrypt --help` parser exists through `build_parser`.
- `--format-kwm` accepts `auto/mp3/flac/m4a/wav`.

- [ ] **Step 2: Implement adapter**

Adapter collects `.kwm`, calls `decode_kwm_file`, and maps target format through `target_format_kwm`.

- [ ] **Step 3: Wire config and CLI**

Add `kuwo` defaults and parser branch. Keep UI disabled until sample verification, but CLI can be experimental.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_cli_parser.py tests/test_config_repository.py tests/test_kuwo_decoder.py -q`

Expected: pass.

### Task 6: Verification and Sample Gate

**Files:**
- Modify tests as needed
- No production code unless a test exposes a real bug

- [ ] **Step 1: Full test run**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Compile check**

Run: `python -m compileall src tests`

Expected: all files compile.

- [ ] **Step 3: Sample scan**

Run a filesystem scan for `.ncm/.kgm/.kgma/.kgg/.vpr/.kwm`. If samples exist, run each platform into a C-drive output directory and validate output with ffmpeg strict decode. If samples do not exist, report that sample-level success is unverified and keep the goal active.

