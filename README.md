<div align="center">

# QKKDecrypt | QQ 酷狗网易云音乐解密工具

<img src="./封面/封面.png" width="320" alt="QKKDecrypt cover">


</div>

## 简介

`QKKDecrypt` 是一款本地音乐文件处理工具，提供桌面 UI 和控制台两种使用方式。默认输出 `mp3`，也支持 `flac`、`m4a`、`wav`。
批量处理会识别已完成的输出文件；确认可播放后跳过，损坏或格式不匹配的文件会重新处理。

## 当前支持的平台

- `QQ音乐`
  - 支持 `.mflac` / `.mgg` / `.mmp4`
  - 默认本地优先解码：优先使用文件内嵌 `ekey` 或本地缓存 `ekey`
  - 缺少 `ekey` 时，本地算法无法凭空还原 key；可在有 QQ 音乐登录态的机器上补取并缓存后再本地解码
  - 旧 Frida 运行期链已移除，不再要求为解密注入 QQ 音乐进程
  - 可输出 `mp3` / `flac` / `m4a` / `wav`，默认转为 `mp3 320 kbps`
- `酷狗音乐`
  - 支持 `.kgm` / `.kgma` / `.kgg` / `.vpr` / `.kgm.flac` / `.vpr.flac`
  - 文件级离线解密，`.kgg` 需要本机 `KGMusicV3.db`
  - 可输出 `auto` / `mp3` / `flac` / `m4a` / `wav`
- `网易云音乐`
  - 支持 `.ncm`
  - 文件级离线解密，默认使用本地流式解码，减少大文件内存占用
  - 可输出 `auto` / `mp3` / `flac` / `m4a` / `wav`
- `酷我音乐`
  - 支持 `.kwm` / `.kwma` / `.kwm.flac`
  - 可输出 `auto` / `mp3` / `flac` / `m4a` / `wav`

## 一键部署

打开 PowerShell，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/BloomingProsperity/Music/music-gateway/deploy.ps1 | iex"
```

如果电脑没有 Python 3.10+，脚本会尝试通过 `winget` 安装 Python 3.12。
重复部署或客户端更新时只同步变化文件，不下载仓库 zip；本地已有 `ffmpeg` 时不会重新下载。

默认安装到：

```text
%USERPROFILE%\QKKDecrypt
```

以后可双击桌面上的 `QKKDecrypt UI`，或手动运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\QKKDecrypt\run-ui.ps1"
```

## 批量转码

控制台支持独立批量转码，可直接运行：

```powershell
python main.py transcode-batch --input D:\music --output D:\mp3 --rule 全部:mp3::320 --max-workers 2
```

如果源文件在 U 盘或移动硬盘，推荐把输出目录放到 C 盘，减少移动盘反复读写：

```powershell
python main.py qq decrypt --input D:\ --output C:\qkk_mp3 --format-mflac mp3 --bitrate 320 --transcode-workers 2 --no-embed-cover
```

`--transcode-workers` 和 `--max-workers` 接受正整数，不再限制为 4。机械盘/U 盘建议 `1` 或 `2`，CPU 和 SSD 都有余量时再提高。

可选采样率：`22050` / `32000` / `44100` / `48000` / `88200` / `96000` Hz。
可选码率：`96` / `128` / `160` / `192` / `256` / `320` kbps。

## 本机自检

部署后可以先跑一遍自检。它会生成本地合成样本，验证 QQ、酷狗、网易云、酷我的解密、转码和严格解码链路：

```powershell
python main.py self-test --output C:\qkk_self_test --bitrate 128 --max-workers 1
```

自检样本只用于确认本机环境和算法链路可运行，不代表所有真实文件都已覆盖。

## 样本验证

要确认真实样本是否能最终输出可播放的 mp3，可以用 `sample-verify`。它会扫描指定目录，按平台解密为 mp3，再用 ffmpeg 严格解码输出文件；没有样本时会明确报告未验证。

```powershell
python main.py sample-verify --input D:\music --output C:\qkk_sample_verify --platform all --bitrate 320 --max-workers 2 --fresh
```

也可以只验证某几个平台：

```powershell
python main.py sample-verify --input C:\music --input D:\music --output C:\qkk_sample_verify --platform netease --platform kuwo
```

每次验证都会在输出目录生成 `sample_verify_report.json` 和 `sample_verify_report.txt`，用于回看候选数量、严格解码通过数量、失败原因和已验证输出文件路径。
加 `--fresh` 会先清空对应平台的验证输出子目录，再重新解密、转码和严格解码。

## 使用边界

### 你应当只在这些前提下使用本项目
- 仅处理你本人拥有**合法访问权限**的本地文件
- 自行确认你的使用行为符合所在地法律、版权规则、平台协议和组织政策
- 不要把本项目用于批量分发、倒卖、牟利或规避付费授权

### 项目不承诺
- 不承诺适用于所有地区、所有平台规则、所有用途
- 不承诺一定符合你所在地区的合规要求
- 不承诺任何特定商业用途可直接使用
- 不为用户的侵权、违约或违规使用承担责任

## 第三方组件说明

请同时阅读：
- [THIRD_PARTY_LICENSES.md](./THIRD_PARTY_LICENSES.md)

当前需要特别注意：
- `PySide6`
- `PySide6-Fluent-Widgets`
- `FFmpeg`
- 其他运行依赖

## 致谢

- QQ 音乐解密模型思路参考项目：
  - [`qqmusic_decrypt`](https://github.com/luyikk/qqmusic_decrypt)
- 网易云音乐解密模型参考 `ncmdump` 相关实现思路
- 其他平台逻辑参考公开资料和本地样本验证

## 许可证

本仓库源码按 **GNU GPL v3** 发布：
- [LICENSE](./LICENSE)

如果你计划进行商业使用、闭源分发或接入额外第三方组件，请先自行完成完整的许可证核验和风险评估。
