# 中转管家 · macOS 原生版

用 Swift + SwiftUI 重写的菜单栏版，不再依赖 Python 和 pip 包。界面和功能跟 `../mac/`（Python 版）一致：

- 菜单栏分四组，每组上下两行，中间用竖线分隔：`内存% / MEM`、`CPU温度 / CPU`、`↑速度 / ↓速度`、`$余额 / 今日token`；今日 token 为 0 时不占位，余额那一行自动放大
- 点图标弹出面板：余额大字、本周占用进度条、总额度 / 已用、今日 token、可用内存、CPU 温度、实时网速
- 右键菜单：立即刷新、刷新间隔、夜间暂停、有用量才查余额、用量统计、设置、检查更新、打开日志、退出
- 设置窗口：多账号（地址 / Key）、一键读 magpie 或 CC Switch 的现有配置、测试连接、开机自启开关
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

`build-app.sh` 产出的 `.app` 带 `LSUIElement`，不会在 Dock 里显示图标。登录后自动启动可以在「设置 → 启动 → 开机自启」里勾选。

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
  Thermal.swift     CPU 温度（IOHID 传感器，私有 API）
  LoginItem.swift   开机自启（SMAppService）
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

## 关于 CPU 温度

macOS 没有公开的温度 API，这里走 `IOHIDEventSystemClient` 的传感器服务（Stats、Hot 用的也是这条路）。几点要说明：

- 属于**私有 API**，App Store 不会收；自己编译、自签分发没有影响。符号用 `dlsym` 动态取，不会在编译期链接。
- Apple Silicon 上读的是 `PMU tdie*` / `PMU2 tdie*`（性能核 / 能效核各一组，一般十几个），取平均值，比单读一个核心稳。
- 不需要 root，也不需要额外授权。
- Intel 机型或系统收紧读不到时返回空值，菜单栏和面板里这一项**自动隐藏**，不影响其它功能。

## 关于开机自启

走系统的 `SMAppService.mainApp`，不是自己往 `~/Library/LaunchAgents` 写 plist。区别是前者会出现在「系统设置 → 通用 → 登录项与扩展」里，用户能在同一个地方看到并统一关掉；后者不在那儿显示，卸载时还容易留残留。

- 需要 **macOS 13 及以上**。包的最低版本是 12，所以在 12 上这个开关不显示（只能用老办法拖进登录项）。
- 开关状态不写进 `config.json`，每次打开设置都读系统的真实状态，避免你在系统设置里改过之后两边对不上。
- 本项目的 `.app` 是 ad-hoc 签名，每次重打包 cdhash 都会变，系统有可能把旧登记判为无效。如果重新编译安装后发现它不启动了，进设置把这个开关关掉再打开一次即可。
