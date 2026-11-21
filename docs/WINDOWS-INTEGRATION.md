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

### 最快路径：两条命令

NUC9 上有 git（dvpack 就是这么装的），直接克隆仓库再跑安装器：

```powershell
git clone https://github.com/cpufreestyle/kindle-mac-panel.git C:\kp
powershell -ExecutionPolicy Bypass -File C:\kp\install-win-metrics.ps1
```

> **用「管理员」PowerShell 跑第二条**（Register-ScheduledTask 要提权）。
> 安装器会：检查 PS7 → 把采集脚本复制到 `C:\win-metrics.ps1` → 注册开机自启计划任务
> `win-metrics`（SYSTEM 账号）→ 启动任务 → 自动跑一次自检并打印结果。

成功后 Mac 面板上会出现 **WIN · NUC9 · 在线** 卡片。

### 手动路径（不想用安装器时）

#### 1. 拷脚本

把 `win-metrics.ps1` 放到 NUC9 的 `C:\win-metrics.ps1`（短路径）。

#### 2. 先手工跑一次看输出

```powershell
cd C:\
pwsh -NoProfile -File .\win-metrics.ps1 -Check
```

会打印主机名、接收端、每项采集值、推送内容和推送结果：

```
主机名     : NUC9
接收端     : http://192.168.1.112:18866/api/push/win
CPU        : 23.5
内存       : total=30G used=18.3G pct=61.2
磁盘       : total=1200G used=856G free=344G pct=71.4
温度       : 47
运行时长   : 864123
推送内容   : {"host":"NUC9","cpu":23.5,...}
推送结果   : HTTP 200 OK
```

- `netDownKBs/netUpKBs` 不在 `-Check` 输出里 —— 网速靠两次采样差分，
  第一次没有基线；常驻跑起来后第二次起就有值。
- `tempC` 读不到（多数台式机没有 ACPI 温度区）是正常的。
- Mac 地址**自动发现**，不用手填；发现结果缓存在
  `%LOCALAPPDATA%\win-metrics\mac-ip.txt`。

#### 3. 确认 Mac 收到了

在 Mac 上开面板 http://192.168.1.112:18866/panel.html ，应出现 **WIN · NUC9 · 在线** 卡片。
Mac 侧的推送日志：`/tmp/kindle-win.log`（每条推送记来源 IP 和各值，被拒时记原因）。

#### 4. 装成开机自启（计划任务）

用**管理员** PowerShell：

```powershell
$script = 'C:\win-metrics.ps1'
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $script"
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
> 计划任务默认会限制运行时长，常驻脚本需要
> `-Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero))`。

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
| 面板没有 WIN 卡 | Mac 端 `/tmp/kindle-win.log` —— 有一条记录说明 Windows 推到了（可能是字段不合法），一条没有说明 Windows 没推过来 |
| 面板显示「WIN · 离线」 | 日志里最后一条距今多久；超过 30s 就是 Windows 侧停了 |
| Windows 侧推送失败 | 在 NUC9 上跑 `pwsh -NoProfile -File C:\win-metrics.ps1 -Check`，输出里有失败原因 |
| 找不到 Mac | 加 `-MacIp <Mac-IP>` 显式指定；或确认 Mac 上 `launchctl list \| grep com.kindle.panel` 有输出 |
| 网速一直是 null | 第一次采样没有基线；第二次起才有值（脚本重启后会自动从 `%LOCALAPPDATA%\win-metrics\nic-state.json` 恢复基线） |
| 温度读不到 | 正常，台式机普遍没有 ACPI 温度区 |
| 曲线是空的 | 历史不足 2 点；或采集脚本没在跑 |
| Mac 换网段后失联 | 脚本会自动重新发现（候选列表 + 缓存），推送失败时也会重新发现；仍不行就 `-MacIp` 指定 |

Mac 侧看推送日志：`/tmp/kindle-win.log`（每条推送记来源 IP、host、各值；被拒记原因）。
