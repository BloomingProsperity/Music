# Changelog

All notable changes to this project will be documented in this file.

## 0.21 - 2026-07-05

- 接入新版 Studio Amber UI 主题，优化控件边框、进度面板和运行态动效配色。

## 0.20 - 2026-07-05

- 重构 UI 为深色处理面板，并新增运行态代码雨和处理终端动效。

## 0.19 - 2026-07-04

- QQ 缺少 ekey 时自动尝试启动本机 QQ 音乐；未检测到客户端时提示安装。

## 0.18 - 2026-07-04

- 酷我默认输入目录优先识别 `C:\KwDownload\song`。

## 0.17 - 2026-07-04

- 自动识别酷我默认下载目录 `C:\KwDownload`。
- 优化网易、酷我共享的 XOR 流解密路径。
- 更新提示保持简洁，完成后自动准备重启。

## 0.16 - 2026-07-04

- 修复酷狗 `.kgg` 批量解密时 native key 缓存偶发复用旧 key 的问题。
- 酷我增加 `.kwm.flac` 扫描和输出文件名识别。
