# 中转管家 · macOS 版

和 Windows 版功能对齐，只是形态按 macOS 的习惯换了一下：

| Windows | macOS |
|---|---|
| 任务栏网速条 | **菜单栏文字**（右上角，显示 `↑12K ↓3.4M $64.43 1.0M tok 内存 6.4G`） |
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
- 面板显示网速 / 菜单栏显示速度 / 菜单栏显示余额 / 菜单栏显示今日 token / 菜单栏显示剩余内存
- 开机自启
- 退出

## token 用量

菜单栏的「内存」是**可用内存**（含可回收缓存），4 秒取一次；没装 psutil 时显示 `—`。

面板和菜单栏各显示一份今日用量（`今日 1.01M tok · 217 次`），每 5 秒刷新一次。数据来自本地，不需要中转站的 access token：

| 来源 | 位置 |
|---|---|
| 喜鹊 magpie | `~/.config/magpie/routing/YYYY-MM-DD.jsonl`，网关转发时顺手记的 `usage` |
| CC Switch | `~/.cc-switch/cc-switch.db`，本地代理记录 + 各工具会话日志的扫描结果 |

两边记的是同一批请求的两种视角，按天只取一份（magpie 优先，CC Switch 补它没有的日期），不会重复计数。读取只消费日志新增的部分，5 秒轮询几乎不占 CPU。

面板右键 →「用量统计…」可以看今日 / 本周 / 本月、近 7 天、按月、本月模型拆分。

## 配

配置放在 `~/.config/relay-balance/config.json`，第一次运行自动生成。也可以直接点面板里的 ⚙ 填：

- **名称 / 地址 / Key**：中转站地址填到域名即可（`https://api.example.com`，带不带 `/v1` 都行）
- **每周额度**：默认 100，本周已用 = 周额度 − 总池剩余
- 不想自己填：设置里点「读 magpie」或「读 CC Switch」，从本机已有配置导入
  - magpie：`~/.config/magpie/providers.json`
  - CC Switch：`~/.cc-switch/cc-switch.db` 的 providers 表，会把所有带地址和 Key 的供应商一次导入

Key 只存在本机，不会上传。

## 文件

```
mac/
├── relay_core.py      共用逻辑：配置、额度查询、网速采样、格式化
├── relay_panel.py     置顶面板 + 用量统计窗口（tkinter）
├── relay_menubar.py   菜单栏常驻项 + 菜单（rumps）
├── usage_stats.py     token 用量统计（读 magpie 日志 + CC Switch 库）
├── start.sh           一键启动
└── requirements.txt
```

`usage_stats.py` 和仓库根目录那份是同一个文件，改的时候两边一起改。

panel 和 menubar 是两个进程，各查各的（每 10 分钟才一次请求，无所谓）。menubar 负责开关 panel，共用同一份 `config.json`。

## 说明

- 网速排除 `lo0 / utun / awdl / llw / gif / stf / bridge / ap`，避免本机代理把同一份流量算两遍；en0 / en1 等真实网卡才计数
- 余额接口同 Windows 版：`/v1/dashboard/billing/subscription` + `/v1/dashboard/billing/usage`（`total_usage` 单位是美分）
- 这个端口是在 Windows 上写的，**还没在真 Mac 上跑过**。首次运行如果报错，把报错贴给我，按平台差异修（多半是字体名、`overrideredirect` 或 rumps 的菜单 API 细节）
