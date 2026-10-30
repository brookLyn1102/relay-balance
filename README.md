# 中转管家

给中转站（new-api / one-api 类接口）做的一个 Windows 桌面小工具：**置顶浮窗看额度**，外加**任务栏实时网速**。

- 余额、周额度一眼可见，快用完时变琥珀 / 变红
- 任务栏条常驻，上传 / 下载实时刷新
- 托盘常驻、开机自启、换 Key、多账号切换

![面板](preview-panel.png)

![任务栏条](preview-bar.png)

![设置](preview-settings.png)

## 功能

| 模块 | 说明 |
|---|---|
| 额度面板 | 无边框卡片，可置顶、可拖动、位置自动记忆；大字显示总池剩余，进度条按周额度算占用 |
| 任务栏条 | 透明底只留文字，贴在托盘左侧；上传/下载两行叠放；截图、任务栏重排后会自动抢回层级 |
| 周额度 | 本周已用 = 周额度 − 总池剩余，公司按周给池子补额度，补满即自动归零 |
| 托盘菜单 | 立即刷新、刷新间隔（30 秒 ~ 30 分钟）、网速条开关与微调、设置、退出 |
| 设置窗口 | 管理多组「名称 / 地址 / Key」，可测试连接、可从 magpie 配置一键读取 |
| 其他 | 单实例运行、开机自启、最小化到托盘、窗口位置持久化 |

## 运行

需要 Windows 10/11 + Python 3.9 以上。

```bash
pip install pillow psutil pystray
python balance_widget.py
```

不想要黑框就用 `pythonw.exe`，或者直接双击 `start-widget.vbs`。

生成桌面 / 开始菜单快捷方式（需要 `pywin32`）：

```bash
pip install pywin32
python make_shortcut.py
```

## 配置

首次运行会在同目录生成 `config.json`（已加入 `.gitignore`，不会上传）。参考 `config.example.json`。

```jsonc
{
  "provider": null,        // magpie 里的供应商 id；留空 = 自动取第一个可用供应商
  "interval": 600,         // 余额刷新间隔（秒）
  "accounts": [            // 也可以不用 magpie，自己填中转地址与 Key
    { "name": "我的中转", "base": "https://api.example.com", "key": "sk-xxx" }
  ],
  "active": "我的中转",
  "week_quota": 100.0,     // 每周额度
  "net_panel": true,       // 面板里显示上传/下载
  "bar_mode": "taskbar",   // taskbar 贴任务栏 / float 悬浮条 / off 关闭
  "bar_transparent": true, // 透明底，只留文字
  "bar_stack": true,       // 上传下载两行叠放（关掉是单行横排）
  "bar_dx": 0,             // 任务栏条水平微调
  "bar_dy": 0
}
```

界面里能改的都不用手改文件：右键面板或任务栏条就是全部菜单。

## 数据来源

余额走 new-api 的计费接口（`Authorization: Bearer <key>`）：

- `GET /v1/dashboard/billing/subscription` → `hard_limit_usd` 总额度
- `GET /v1/dashboard/billing/usage` → `total_usage`（单位是美分）

剩余 = 总额度 − 累计已用。

**注意**：`usage` 接口不认 `start_date` / `end_date`，只返回累计值，所以「本周已用」拿不到服务端数据，本项目按「周额度 − 总池剩余」反推。总池比周额度多时按 0 显示；扣成负数时百分比超过 100%，进度条满格变红。

网速用 `psutil.net_io_counters(pernic=True)` 求差，排除 loopback / teredo / isatap，避免本地代理把流量算两遍。

## 实现备忘

Win11 的任务栏是 XAML 合成层，TrafficMonitor 那种把窗口 `SetParent` 挂进 `Shell_TrayWnd` 的做法会被完全遮住。这里的做法是：

1. 独立无边框 `Toplevel` + `-transparentcolor` 抠底，直接浮在任务栏对应位置
2. `SetWindowPos(HWND_TOP)` 再 `HWND_TOPMOST`，因为 `SetWindowBand` 在 Win11 上已失效
3. 每秒扫一次窗口 Z 序，被外部窗口压住就抢回来（截图工具关掉后自动恢复）

另外两个坑：改 `WS_EX_TOOLWINDOW` / `WS_EX_NOACTIVATE` 后必须带 `SWP_FRAMECHANGED` 重排，否则分层窗口只显示底色不显示文字；`ImageTk.PhotoImage` 要复用并 `paste` 原位更新，每帧新建会掉字。

## 许可

MIT
