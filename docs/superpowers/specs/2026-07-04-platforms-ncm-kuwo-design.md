# 网易/酷狗/酷我平台升级设计

## 目标

继续推进 QQ 之外的平台能力：让已有后端的网易云和酷狗进入当前 PySide6 UI，并研究酷我 `.kwm` 的本地解码可行性。最终交付必须能在本机运行，成功输出可播放音频；没有样本的平台不能标记为“已验证成功”。

## 当前事实

- UI 当前只有 QQ 真正可运行，`ui_app.py` 在 `_start_platform` 里硬编码拦截非 QQ 平台。
- `decrypt_service.run_batch` 已经是平台无关批处理框架，QQ、酷狗、网易云 adapter 都符合 `PlatformAdapter` 协议。
- 网易云已有 `.ncm` adapter，使用 `ncmdump-py` 解密，支持输出 `auto/mp3/flac/m4a/wav`。
- 酷狗已有 `.kgm/.kgma/.vpr/.kgg/.kgm.flac/.vpr.flac` adapter。`.kgm/.kgma/.vpr` 依赖 `kugou_key.xz`，本机已找到；`.kgg` 还需要 `KGMusicV3.db`，本机未找到。
- 酷我只有 UI 占位，没有 config、CLI、adapter。外部 `bczhc/kwmusic-kwm-decrypt` C 实现表明一种 `.kwm` 解码模型：跳过 1024 字节头，在数据区寻找 32 字节 key，然后按 `key[i & 31]` XOR 输出；作者只确认过原始 FLAC 型 `.kwm`。
- 本机未发现 `.ncm/.kgm/.kgma/.kgg/.vpr/.kwm` 样本，样本级播放和音质验证暂缺。

## 推荐方案

第一阶段接入网易云和酷狗 UI，不改核心解码算法：

- 新增通用 UI config builder，把 `PlatformRunOptions` 转为任意平台的 `BatchRunConfig`。
- `ui_app.py` 运行入口从 `_run_qq` 改为 `_run_platform`，由 `build_platform_adapter(platform_id)` 选择 adapter。
- 网易云页面状态改为可用，保存/加载 `target_format_ncm`，可按 UI 的转码开关输出目标格式。
- 酷狗页面状态改为可用，但启动前做预检：缺 `kugou_key.xz` 阻止运行；输入包含 `.kgg` 且没有 `KGMusicV3.db` 时阻止运行并写日志。
- 酷我页面保留，但显示“研究中/需要样本”，不在第一阶段假启用。

第二阶段实现酷我原型 adapter：

- 新增 `src/Infrastructure/kuwo_decoder.py`，用 Python 实现已研究到的 KWM XOR 解码。
- 新增 `src/Infrastructure/platforms/kuwo/adapter.py`，收集 `.kwm`，输出 basename，探测实际容器。
- 在 config 和 CLI 中加入 `kuwo`，但默认 UI 状态应为“实验”，只有通过样本验证后改为可用。
- 单元测试使用合成 KWM：构造 1024 字节头、重复 key 区和加密后的最小 FLAC/WAV 片段，证明解码器能还原原始字节。

第三阶段优化算法和性能：

- 网易云短期沿用 `ncmdump-py`，因为它能处理 metadata 和封面；后续可做流式 NCM 解码，避免一次性读完整音乐数据。
- 酷狗已有 C native backend 和 numpy LUT 路径，优化重点放在 UI 预检、错误日志和 `.kgg` 数据库定位，不先重写成熟路径。
- 酷我先 Python 实现并测试正确性；如果样本显示文件大、速度慢，再考虑 C/native 或 chunked XOR。

## 错误处理

- 输入路径不存在、输出目录不可写，在启动前拦截。
- 未找到候选文件时，显示完成但成功数为 0，并写日志说明候选数。
- 酷狗缺 key 或 `.kgg` 缺 DB 时，不启动批处理，避免跑到一半失败。
- 酷我未验证前，UI 不允许声称可用；如果 CLI 实验入口存在，输出日志要包含 `experimental=true`。

## 验证标准

- 自动化：`python -m pytest -q` 全通过。
- UI 状态：网易云、酷狗可启动；酷我仍清楚标记为实验/不可用。
- CLI 参数：`netease decrypt`、`kugou decrypt` 仍正常；加入酷我后 `kuwo decrypt --help` 正常。
- 样本验证：每个平台至少一个真实样本完成解密和目标格式转换后，用 ffmpeg strict decode 验证输出可读；没有样本的平台不能计为完成。

