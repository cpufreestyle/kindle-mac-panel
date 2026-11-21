# Windows 主机接入（运维值班台 · WIN 卡）

把局域网里的 Windows 机器（当前目标：**NUC9 = 192.168.1.68**）的 CPU / 内存 / 磁盘 / 网速
并进 Mac 上的运维值班台面板，和 Mac 本机指标同屏显示。

---

## 架构

```
NUC9 (Windows)                        Mac (192.168.1.112)
┌─────────────────────┐              ┌──────────────────────────────┐
│ win-metrics.ps1     │  POST JSON   │ server.py                    │
│ 每 5s 采集一次       │ ───────────▶ │ POST /api/push/win           │
│                     │              │   → 缓存最新快照 + 追加历史    │
│                     │              │   → 面板渲染 WIN 卡           │
└─────────────────────┘              └──────────────────────────────┘
```

**为什么是「Windows 主动推」而不是「Mac 去拉」**：
NUC9 的 SSH(22) 需要密码、SMB(445) 需要认证、WinRM(5985) 没开，Mac 侧都拉不动。
反向推送只需要 NUC9 能出站访问 Mac 的 18866（已实测可达），且不需要在 Windows 上开新端口。

数据超过 30 秒没更新 → 面板显示「WIN · 离线」；从未收到过数据 → 面板不显示 WIN 卡。

---

## 一、在 NUC9 上部署（一次性）

NUC9 上已有 Jellyfin(8096) 和 dvpack(8899)，用 **PowerShell 7**（`pwsh`）。

### 1. 拷脚本

在 Mac 上把 `win-metrics.ps1` 传到 NUC9。任选一种：

- **共享文件夹**：NUC9 上开一个共享目录，Mac 挂载后拷过去
- **U 盘 / 网盘**：同上
- 或者直接在 NUC9 上新建文件粘贴内容（脚本不长）

放到 `C:\dvpack\` 或其他短路径下，例如 `C:\win-metrics.ps1`。

### 2. 先手工跑一次看输出

```powershell
cd C:\
pwsh -NoProfile -File .\win-metrics.ps1 -MacIp 192.168.1.112 -Once
```

应打印一行 JSON，形如：

```json
{"host":"NUC9","cpu":12.5,"memPct":58.3,"memUsedGB":17.5,"memTotalGB":30.0,
 "diskPct":71.2,"diskUsedGB":855.0,"diskTotalGB":1200.0,"diskFreeGB":345.0,
 "netDownKBs":null,"netUpKBs":null,"tempC":46.0,"uptimeSec":864123}
```

- `netDownKBs` 首次是 `null` 属正常 —— 速率靠前后两次采样差分，第一次没有基准。
- `tempC` 读不到（多数台式机没有 ACPI 温度区）是正常的，不影响其他指标。

### 3. 确认 Mac 收到了

在 Mac 上开面板 http://192.168.1.112:18866/panel.html ，应出现 **WIN · NUC9 · 在线** 卡片。

### 4. 装成开机自启（计划任务）

用**管理员** PowerShell：

```powershell
$script = 'C:\win-metrics.ps1'
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $script -MacIp 192.168.1.112"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
Register-ScheduledTask -TaskName 'win-metrics' -Action $action -Trigger $trigger `
    -Principal $principal -Description '推送 Windows 指标到 Mac 运维值班台' -Force
```

立即启动验证：

```powershell
Start-ScheduledTask -TaskName 'win-metrics'
```

> 用 SYSTEM 账号 + `AtStartup` 触发：开机就跑，不依赖用户登录。
> `pwsh.exe` 需在 PATH 里；若装在非默认位置，把 `-Execute` 改成完整路径
> （如 `C:\Program Files\PowerShell\7\pwsh.exe`）。

### 5. 防火墙

出站 POST 到 Mac 的 18866 通常不受 Windows 防火墙拦（出站默认允许）。
若被拦，加一条出站规则：

```powershell
New-NetFirewallRule -DisplayName 'win-metrics out' -Direction Outbound `
    -RemoteAddress 192.168.1.112 -RemotePort 18866 -Protocol TCP -Action Allow
```

---

## 二、Mac 侧

已在 `server.py` 内置，无需额外配置：

| 位置 | 内容 |
|---|---|
| `POST /api/push/win` | 接收 JSON 推送 |
| `win_push()` / `win_snapshot()` | 快照缓存 + 30s TTL 离线判定 |
| `_win_card_html()` | 渲染 WIN 卡（CPU/内存/磁盘 文本 + 两条曲线） |
| `HIST_WCPU/WMEM/WDISK/WNETD/WNETU` | Windows 历史（240 点） |
| `/sparkpng/wincpu.png` `/sparkpng/winnet.png` | 曲线图（仅浏览器端用） |
| `render_panel(kindle=...)` | 按 User-Agent 分流：curl=Kindle，浏览器=完整版 |

Mac 的 18866 已绑定 `0.0.0.0`，macOS 防火墙当前关闭，局域网内可直接访问。

### 两种呈现形态

Kindle 上显示的 index.html 是 fetchloop 用 **curl** 抓的，浏览器是浏览器 UA，
服务端据此分流：

| | WIN 卡 | 滚动 |
|---|---|---|
| **Kindle**（curl） | 两行纯文字（无曲线图） | 固定一屏，不滚动 |
| **浏览器** | 文字 + CPU/内存曲线 + 网速曲线 | 页面可滚动 |

墨水屏只有 1072×1448 且 `overflow:hidden` 不滚动，完整 WIN 卡（351px）会把
底部建议行裁掉，所以 Kindle 版只给文字（净高 1279px，留 169px 余量）。

---

## 三、采集项

| 字段 | 来源 | 说明 |
|---|---|---|
| `cpu` | `Win32_PerfFormattedData_PerfOS_Processor` | 总占用 %，回退 Get-Counter → LoadPercentage |
| `memPct` / `memUsedGB` / `memTotalGB` | `Win32_OperatingSystem` | 物理内存 |
| `diskPct` / `diskUsedGB` / `diskTotalGB` / `diskFreeGB` | `Win32_LogicalDisk`（系统盘） | |
| `netDownKBs` / `netUpKBs` | `Win32_PerfRawData_Tcpip_NetworkInterface` | 物理网卡合计，**两次采样差分**算速率 |
| `tempC` | `root/wmi` 的 `MSAcpi_ThermalZoneTemperature` | 多数机器读不到，读不到就不发 |
| `uptimeSec` | `Win32_OperatingSystem.LastBootUpTime` | |

---

## 四、排障

| 现象 | 查什么 |
|---|---|
| 面板没有 WIN 卡 | Mac 端 `curl -X POST -d '{"host":"x","cpu":1}' http://127.0.0.1:18866/api/push/win` → 应返回 `OK`；返回 400 说明字段不合法 |
| 面板显示「WIN · 离线」 | NUC9 上计划任务没跑：`Get-ScheduledTask -TaskName win-metrics`，看 `LastRunTime` / `LastTaskResult` |
| 推送失败 | NUC9 手工跑 `-Once`，看是否报连接错误；确认 Mac IP 没变（Mac 换网段时 IP 会变，`ipconfig getifaddr en0` 查） |
| 网速一直是 null | 第一次采样没有基准，第二次起才有值 |
| 温度读不到 | 正常，台式机普遍没有 ACPI 温度区 |
| 曲线是空的 | 历史不足 2 点；或采集脚本没在跑 |

Mac 侧看推送日志：`/tmp/kindle-panel.log`（launchd StandardOut）。
