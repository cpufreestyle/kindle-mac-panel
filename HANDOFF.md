# HANDOFF v2 (2026-11-22) — NUC9 已验证在线 — 项目迁到 `AI Shared/repo/` + 运维值班台改名 + Windows 主机接入

## 一句话结论

**项目已从 `~/kindle-panel` 迁到 `/Users/a1-6/AI Shared/repo/kindle-panel`（旧路径留符号链接兼容），面板标题改为「运维值班台」，并新增局域网 Windows 主机（NUC9）的指标接入 —— Mac 侧全部就绪，NUC9 已验证在线（WIN 卡 CPU/内存/磁盘实时显示）。**

---

## 1. 本次改动

### 1.1 目录迁移（2026-11-22）

| | |
|---|---|
| 新位置 | `/Users/a1-6/AI Shared/repo/kindle-panel` |
| 旧位置 | `/Users/a1-6/kindle-panel` → **符号链接指向新目录** |
| 为什么留符号链接 | launchd 的 9 个 plist、`gitpush.sh`、外部脚本引用都写死了旧路径；符号链接让它们全部继续有效，迁移零风险、可秒级回滚 |
| 注意 | 目录名带空格，命令行里必须加引号 |

**代码里的绝对路径已清零**（`grep -rn "/Users/a1-6/kindle-panel"` 无结果）：

- `server.py`：`HIST_FILE` 改成 `os.path.join(BASE, "hists.json")`（BASE 已经是 `dirname(abspath(__file__))`）
- `gitpush.sh`：`cd "$(dirname "$0")"`
- `push-watchdog.py`：`os.path.join(dirname(abspath(__file__)), 'watchdog-v33.sh')`
- `mk-laytest.py`：`sys.path.insert(0, dirname(abspath(__file__)))`
- 11 个 plist（仓库模板 5 个 + 已安装 6 个）全部改成新路径

**迁移后已验证**：
- 进程实际跑 `/Users/a1-6/AI Shared/repo/kindle-panel/server.py`（`ps` 确认）
- `hists.json` 写到新目录，且与符号链接路径是同一文件（`os.path.samefile`）
- 6 个 LaunchAgent 全部 `bootout` + `bootstrap` 重载成功
- `/win/` 下载路由、`panel.html` 的 curl-UA 分流都正常

### 1.2 面板改名「运维值班台」

- `server.py` 的 `<title>`、面板显示标题（`panel_title`）、24h 历史页标题+页头
- qstats 联动时仍显示 `QSTATS`（那是数据源标识，不是项目名）

### 1.3 comp 显示 0 的 bug 修复（本轮最实的技术债）

**现象**：RAM 卡上 `Comp` 恒为 0，`Free` 由 total 反推导致四项加起来 ≠ 总内存。

**根因**：`server.py` 里 `mem_app, mem_wired, mem_comp = vm.used, 0.0, 0.0` —— wired/comp 只在读到
qstats 快照时才被赋值；而本机模式（qstats 未运行）下 psutil 在 macOS 上**不提供 compressed**，
且 `vm.used = active + wired`（不含 compressed），与 qstats 的口径不同。

**修法**：本机模式改用 `vm_stat` 按 qstats 同口径推导：

```
free = free_pages + speculative + file_backed(cache)
used = total - free
app  = used - wired - compressed
```

新增 `mem.freeGB` 字段，面板 Free 直接取该值不再反推。口径用 `top -l 1 | grep PhysMem`
校验过（wired/compressor 与 top 一致）。

### 1.4 Windows 主机接入（WIN 卡）

**架构**：NUC9（`192.168.1.68`，Windows 10/11，SMB banner 自报 "NUC9"）上的
`win-metrics.ps1` 每 5s 把 JSON POST 到 Mac 的 `POST /api/push/win`；
Mac 端缓存快照 + 30s TTL 判离线 + 渲染 WIN 卡。

**为什么是「Windows 主动推」而不是「Mac 去拉」**：NUC9 的 SSH(22) 要密码、SMB(445) 要认证、
WinRM(5985) 没开，Mac 侧三种都拉不动；反向推送只需 NUC9 能出站访问 Mac 的 18866
（已实测可达，Mac 防火墙关闭、18866 绑 0.0.0.0）。

**Kindle / 浏览器必须分流**（最隐蔽的坑）：Kindle 上的 index.html 是 fetchloop 用 **curl** 抓的
（User-Agent=curl/…），浏览器是浏览器 UA，服务端据此分流：

| | WIN 卡 | 滚动 |
|---|---|---|
| **Kindle**（curl） | 两行纯文字（无曲线图） | 固定一屏，不滚动 |
| **浏览器** | 文字 + CPU/内存曲线 + 网速曲线 | 页面可滚动 |

墨水屏只有 1072×1448 且 `overflow:hidden` 不滚动，完整 WIN 卡（351px）会把底部建议行裁掉，
所以 Kindle 版只给文字（净高 1279px，留 169px 余量）。

**连带修正**：原先 `html,body{height:100%}` 会让 body 变成滚动容器、html 高度锁死，
fullPage 截图和 `window.scrollTo` 全失效 → 改成 `min-height:100%` 让文档自然增长，
Kindle 版再显式锁回 `html.k{height:100%;overflow:hidden}`。
分流用的 class 必须加在 **`<html>`** 上（加在 body 上与 `html.k` 选择器对不上，不生效）。

---

## 2. 现在能做什么

### Mac 侧（全部就绪，已验证）

- 面板 http://192.168.1.112:18866/panel.html —— 浏览器版带 WIN 卡曲线
- 24h 历史 http://192.168.1.112:18866/history
- Kindle 自动拉取（fetchloop 15s 一轮），Kindle 版是文字版 WIN 卡
- 推送日志 `/tmp/kindle-win.log`（每条记来源 IP、host、各值；被拒记原因）
- Windows 脚本下载 `http://<Mac-IP>:18866/win/win-metrics.ps1`（白名单两个文件）

### NUC9 侧（**已验证在线**，常驻运行）

在 NUC9 的**管理员** PowerShell：

```powershell
iwr http://192.168.1.112:18866/win/install-win-metrics.ps1 -OutFile C:\iwm.ps1; powershell -ExecutionPolicy Bypass -File C:\iwm.ps1
```

> **仓库是 PRIVATE**，`git clone https://github.com/...` 在 NUC9 上会失败（那边没 GitHub 凭据），
> 所以走 Mac 提供文件下载（NUC9 → Mac:18866 本来就通，采集也要用这个通道）。

安装器会：检查 PS7 → 从 Mac 下载采集脚本 → 注册开机自启计划任务（SYSTEM 账号，
`ExecutionTimeLimit=0`，常驻脚本默认的运行时长限制会把它掐掉）→ 启动 → 自动自检并打印结果。

装完 Mac 面板出现 `WIN · NUC9 · 在线`。<br>**当前状态**：`win-metrics.ps1` 已在 NUC9 常驻后台运行（后台 pwsh，每 5s 推送），Mac 面板及 Kindle 均显示 `WIN · 泗塘NUC9 · 在线`（CPU/内存/磁盘实时刷新）。

---

## 3. 工具箱（本次新增/修复）

| 脚本 | 作用 |
|---|---|
| `snap.py` | 抓 Kindle 屏幕存 PNG。**帧缓冲是 1088×6144 / 8bpp 灰度**（4 页滚动缓冲），不是 32bpp —— 按 32bpp 解会满屏横向乱码。`--relaunch` / `--settle` |
| `usb-connect.sh` | Kindle USB 接驳：等新网卡 → 配 `192.168.15.201` → 等 telnet:23 通。**这个静态 IP 从来没有脚本配过**，以前每次插线都得手动 ifconfig |
| `win-metrics.ps1` | Windows 侧采集器（PS7）。Mac 地址自动发现（Mac 的 en0 IP 会在 `192.168.1.x`/`192.168.15.x` 间切换，写死会失联）；`-Check` 诊断模式 |
| `install-win-metrics.ps1` | Windows 侧一键安装器 |
| `docs/WINDOWS-INTEGRATION.md` | Windows 接入完整手册（部署/排障/采集项） |
 | `nuc9-install-win-metrics.ps1` | NUC9 一键安装脚本（从 Mac 下载 `install-win-metrics.ps1` 并运行，等价于 `iwr http://<Mac-IP>:18866/win/install-win-metrics.ps1 -OutFile C:\iwm.ps1; powershell -ExecutionPolicy Bypass -File C:\iwm.ps1`） |

---

## 4. 注意事项

- **Kindle 与 Mac 的 IP 都是会变的**：
  - Mac en0 在 `192.168.1.112`（WiFi）和 `192.168.15.201`（USB RNDIS）之间切
  - Windows 采集脚本会自动发现 + 缓存（`%LOCALAPPDATA%\win-metrics\mac-ip.txt`），推送失败时重新发现
  - Kindle 侧 fetchloop 靠 `lan.txt` 探测 Mac，也有候选列表
- **`_hsave` 加了锁**：采样线程和 HTTP 推送线程都会调，原先无锁时 `json.dump` 遍历 list 会撞上
  另一线程的 append，可能抛 `list changed size during iteration` 并写坏 hists.json。
  现在加锁 + 拷快照 + `tempfile` 原子替换（保持原文件权限）。
- **`_vmstat_mem()` 整体返回 None**：任一项解析不出来就整体放弃，避免半份数据导致四段不自洽。
- **Kindle 的 JS 执行随机失效**（见 `kindle-illusion-waf-app-recipe` 记忆），所以面板的文字刷新
  靠服务端渲染 + 8s 轮询 spark.json，不依赖复杂 JS。
- **e-ink 刷完要等 12–30s** 再截图，否则拍到刷新中途的残影。

---

## 5. 下一步（按优先级）

1. **NUC9 已验证在线**：`win-metrics.ps1` 常驻后台运行（pwsh，每 5s 推送），Mac 面板和 Kindle 均显示 `WIN · 泗塘NUC9 · 在线`。如需重装，执行 `nuc9-install-win-metrics.ps1`（管理员 `pwsh`）或等价执行 `iwr http://<Mac-IP>:18866/win/install-win-metrics.ps1 -OutFile C:\iwm.ps1; powershell -ExecutionPolicy Bypass -File C:\iwm.ps1`。
2. NUC9 通了以后验证：浏览器面板有 WIN 卡曲线、Kindle 上有两行 WIN 文字、`/tmp/kindle-win.log` 有 `.68` 的记录
3. 想让 Mac 主动拉（不装 Windows 计划任务）的话，需要配一次免密 SSH
4. `alert.sh` 目前只盯 Mac 本机指标 —— Windows 侧离线要不要告警，等定

---

## 6. 相关记忆

- [[kindle-panel-windows-integration]] —— 本次的架构与坑位（UA 分流、内存口径、反向推送的原因）
- [[kindle-illusion-waf-app-recipe]] —— 动态图表=PIL 多序列曲线 PNG + img 双缓冲；JS 执行不稳定
- [[kindle-pw4-usbnet-install-state]] —— 设备侧通道；U 盘模式死结（`diskutil eject` 后 ~75s 恢复）



