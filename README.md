# Music Gateway

Windows 本地音乐解密与转码客户端。

当前版本：`0.22`

## 支持平台

| 平台 | 输入格式 | 输出格式 | 说明 |
| --- | --- | --- | --- |
| QQ 音乐 | `.mflac` / `.mgg` / `.mmp4` | `mp3` / `flac` / `m4a` / `wav` | 优先使用文件内嵌 ekey 或本地缓存 ekey；缺少 ekey 时需要有 QQ 音乐登录态的机器补取 |
| 酷狗音乐 | `.kgm` / `.kgma` / `.kgg` / `.vpr` / `.kgm.flac` / `.vpr.flac` | `auto` / `mp3` / `flac` / `m4a` / `wav` | 文件级离线解密；`.kgg` 需要本机 `KGMusicV3.db` |
| 网易云音乐 | `.ncm` | `auto` / `mp3` / `flac` / `m4a` / `wav` | 文件级离线解密；使用本地流式解码 |
| 酷我音乐 | `.kwm` / `.kwma` / `.kwm.flac` | `auto` / `mp3` / `flac` / `m4a` / `wav` | 文件级离线解密 |

## 主要功能

- 桌面 UI 批量处理
- 解密和转码分离进度
- 解密完成后自动进入转码队列
- 已转换文件识别，成品可播放时自动跳过
- 输出目录一键打开
- 按音乐作者分类输出
- 完成后删除源文件，且只在成品确认成功后执行
- 转码并发数由用户控制
- 支持采样率和码率设置
- 支持一键更新，更新完成后自动重启

## 一键部署

在 Windows PowerShell 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/BloomingProsperity/Music/music-gateway/deploy.ps1 | iex"
```

默认安装目录：

```text
%USERPROFILE%\QKKDecrypt
```

部署完成后可以从桌面快捷方式启动 `QKKDecrypt UI`。

重复部署或客户端更新时只同步变化文件。本机已有 `ffmpeg` 时不会重复下载。

## UI 使用

1. 选择平台。
2. 选择输入路径。
3. 选择输出目录。
4. 选择输出格式。
5. 设置采样率、码率、并发数等选项。
6. 点击开始。

处理完成后，输出目录里会生成可播放的目标格式文件。

## 命令行示例

QQ 音乐转 mp3：

```powershell
python main.py qq decrypt --input D:\music --output C:\QKKDecrypt\output --format-mflac mp3 --format-mgg mp3 --format-mmp4 mp3 --bitrate 320 --transcode-workers 2
```

网易云音乐转 flac：

```powershell
python main.py netease decrypt --input D:\CloudMusic\VipSongsDownload --output C:\QKKDecrypt\output --format-ncm flac --transcode-workers 2
```

酷狗音乐转 m4a：

```powershell
python main.py kugou decrypt --input C:\KuGou --output C:\QKKDecrypt\output --format-kgma m4a --format-kgg m4a --transcode-workers 2
```

酷我音乐转 wav：

```powershell
python main.py kuwo decrypt --input C:\KwDownload\song --output C:\QKKDecrypt\output --format-kwm wav --transcode-workers 2
```

独立批量转码：

```powershell
python main.py transcode-batch --input D:\music --output C:\QKKDecrypt\converted --rule 全部:mp3::320 --max-workers 2
```

## 自检

生成本地合成样本，并验证 QQ、酷狗、网易云、酷我的解密、转码和严格解码链路：

```powershell
python main.py self-test --output C:\qkk_self_test --bitrate 128 --max-workers 1
```

自检用于确认当前电脑环境和算法链路可运行。

## 真实样本验证

扫描指定目录，把真实样本解密并转码为 mp3，再用 ffmpeg 严格解码验证：

```powershell
python main.py sample-verify --input D:\music --output C:\qkk_sample_verify --platform all --bitrate 320 --max-workers 2 --fresh
```

验证报告会生成在输出目录：

```text
sample_verify_report.json
sample_verify_report.txt
```

## 参数范围

采样率：

```text
22050 / 32000 / 44100 / 48000 / 88200 / 96000 Hz
```

码率：

```text
96 / 128 / 160 / 192 / 256 / 320 kbps
```

移动硬盘或 U 盘作为输入源时，建议把输出目录放到 C 盘，并把并发数设置为 `1` 或 `2`。

## 使用边界

本项目只用于个人合法获取音频的备份和格式转换。

请自行确认使用行为符合所在地法律、版权规则、平台协议和组织政策。不要把本项目用于批量分发、倒卖、牟利或规避付费授权。

## 致谢

本项目保留并重构了部分公开项目和资料中的实现思路。原始项目与参考资料请见：

- [Acooldog / QQKWKG-TriMusicDecrypt](https://github.com/Acooldog/QQKWKG-TriMusicDecrypt)
- [luyikk / qqmusic_decrypt](https://github.com/luyikk/qqmusic_decrypt)
- `ncmdump` 相关实现

第三方组件和许可证信息见：

- [THIRD_PARTY_LICENSES.md](./THIRD_PARTY_LICENSES.md)

## 许可证

本仓库源码按 GNU GPL v3 发布：

- [LICENSE](./LICENSE)
