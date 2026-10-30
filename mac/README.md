# 中转管家 · macOS 版

和 Windows 版功能对齐，只是形态按 macOS 的习惯换了一下：

| Windows | macOS |
|---|---|
| 任务栏网速条 | **菜单栏文字**（右上角，显示 `↑12K ↓3.4M $64.43`） |
| 系统托盘图标 | 一个菜单栏常驻项，点开是菜单 |
| 置顶浮窗面板 | 同样的置顶深色卡片，可拖动、位置记忆 |
| 开机自启（注册表） | LaunchAgent |

## 装

```bash
cd mac
pip3 install --user psutil rumps
python3 relay_menubar.py
```

或者直接：

```bash
bash start.sh        # 自动检查依赖并启动
```

启动后菜单栏右侧会出现 `↑0B ↓0B $—`，点它就有菜单：

- 显示面板 / 隐藏面板
- 立即刷新余额
- 刷新间隔（30 秒 ~ 30 分钟）
- 面板显示网速 / 菜单栏显示速度 / 菜单栏显示余额
- 开机自启
- 退出

## 配

配置放在 `~/.config/relay-balance/config.json`，第一次运行自动生成。也可以直接点面板里的 ⚙ 填：

- **名称 / 地址 / Key**：中转站地址填到域名即可（`https://api.example.com`，带不带 `/v1` 都行）
- **每周额度**：默认 100，本周已用 = 周额度 − 总池剩余
- 不想自己填，且本机装了 magpie：设置里点「从 magpie 读取」

Key 只存在本机，不会上传。

## 文件

```
mac/
├── relay_core.py      共用逻辑：配置、额度查询、网速采样、格式化
├── relay_panel.py     置顶面板（tkinter）
├── relay_menubar.py   菜单栏常驻项 + 菜单（rumps）
├── start.sh           一键启动
└── requirements.txt
```

panel 和 menubar 是两个进程，各查各的（每 10 分钟才一次请求，无所谓）。menubar 负责开关 panel，共用同一份 `config.json`。

## 说明

- 网速排除 `lo0 / utun / awdl / llw / gif / stf / bridge / ap`，避免本机代理把同一份流量算两遍；en0 / en1 等真实网卡才计数
- 余额接口同 Windows 版：`/v1/dashboard/billing/subscription` + `/v1/dashboard/billing/usage`（`total_usage` 单位是美分）
- 这个端口是在 Windows 上写的，**还没在真 Mac 上跑过**。首次运行如果报错，把报错贴给我，按平台差异修（多半是字体名、`overrideredirect` 或 rumps 的菜单 API 细节）
