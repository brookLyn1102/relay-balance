# 中转管家 · macOS 原生版

用 Swift + SwiftUI 重写的菜单栏版，不再依赖 Python 和 pip 包。界面和功能跟 `../mac/`（Python 版）一致：

- 菜单栏显示 `↑速度 ↓速度  $余额  今日 token  内存`
- 点图标弹出面板：余额大字、本周占用进度条、总额度 / 已用、今日 token、可用内存、实时网速
- 右键菜单：立即刷新、刷新间隔、夜间暂停、有用量才查余额、用量统计、设置、检查更新、打开日志、退出
- 设置窗口：多账号（地址 / Key）、一键读 magpie 或 CC Switch 的现有配置、测试连接
- 用量统计：今日 / 本周 / 本月、近 7 天、按月、本月按模型
- 夜间 21:00–08:30 不查额度（只停余额请求，网速 / token / 内存照常走本地）

配置文件与 Python 版共用 `~/.config/relay-balance/config.json`，两边可以互相接管，不会互相破坏。

## 要求

macOS 12 及以上 + Xcode 命令行工具（`xcode-select --install`）。不需要装 Python，也不需要第三方库。

## 编译

```bash
cd mac-native
swift build -c release          # 只编译，产物在 .build/release/RelayBalance
./scripts/build-app.sh          # 编译并打成「中转管家.app」
open ".build/中转管家.app"
```

`build-app.sh` 产出的 `.app` 带 `LSUIElement`，不会在 Dock 里显示图标。开机自启把它拖进「系统设置 → 通用 → 登录项」即可。

想用 Xcode 调试：`open Package.swift`，选 `RelayBalance` scheme 直接 Run。

## 目录

```
Package.swift
Sources/RelayBalance/
  main.swift        入口、状态栏、右键菜单、窗口管理
  Config.swift      配置读写、magpie / CC Switch 凭据解析
  Balance.swift     额度查询（URLSession）、周额度换算、格式化
  Usage.swift       token 统计（magpie jsonl + CC Switch sqlite）
  System.swift      网速（getifaddrs）、内存（host_statistics64）
  Model.swift       状态与定时刷新
  PanelView.swift   主面板
  SettingsView.swift  设置
  UsageView.swift   用量统计
scripts/build-app.sh
```

## 与 Python 版的差异

| | Python 版 | 原生版 |
|---|---|---|
| 运行环境 | python3 + rumps + psutil + pillow | 无依赖，单个可执行文件 |
| 菜单栏 | rumps 标题，不能单独上色 | NSStatusItem，可用 attributedTitle 上色 |
| 面板 | 独立 tkinter 浮窗 | NSPopover，跟随菜单栏图标 |
| 内存占用 | 两个进程（菜单栏 + 面板）约 80MB | 单进程，约 30MB |

Python 版保留在 `../mac/`，随时可以回退。
