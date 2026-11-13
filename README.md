# Kindle 墨水屏 Mac 监控仪表

Mac 状态实时显示在 Kindle Paperwhite 4 上：应用面板 + 睡眠屏保双呈现，
监控 → 解读 → 建议 → 推送四层闭环，全链路自愈。

> **前提**：Kindle 已越狱并部署 WAF 应用框架（mesquite）+ USBNet watchdog。
> 采集/渲染/告警全自动，日常零维护。

## 快速开始

```bash
# 1. 安装 LaunchAgent（符号链接到 ~/Library/LaunchAgents/）
for f in launchagents/com.kindle.*.plist; do
  ln -sf "$PWD/$f" ~/Library/LaunchAgents/
  launchctl bootstrap "gui/$(id -u)" "$f"
done

# 2. 体检（9 项端到端自检）
python3 check.py

# 3. 完成。面板地址 http://<Mac-IP>:18866/panel.html
#    24h 历史 http://<Mac-IP>:18866/history
```

配置：`city.txt` 天气城市 · `bark-url.txt` 手机推送 URL（可选）。

## 架构

```
采集（三个独立 launchd，文件传递，server 零写盘零外联）
  sensors.sh  每 30s   SMC/ioreg      → /tmp/kindle-sensors.json
  wx.sh       每 30min wttr.in        → /tmp/kindle-wx.json
  alert.sh    每 5min   psutil 采样    → history.json + macOS 通知告警
      ↓
渲染  server.py :18866（psutil + 上述 JSON 文件）
  /panel.html       应用面板（建议引擎 + 防抖）
  /screensaver.png  屏保仪表图 1072×1448（Pillow + STHeiti）
      ↓ 15s
传输  USB RNDIS（Mac 192.168.15.201 持久化 / Kindle 192.168.15.244）
      ↓
呈现  Kindle fetchloop.sh（常驻，15s）
  ├ 拉面板 → /var/local/mesquite/com.kindle.macpanel/index.html
  ├ 拉屏保图 → md5 变化才 mntroot rw 写 /usr/share/blanket/screensaver/bg_ss00.png
  └ 失败 → OFFLINE 提示页（恢复自动回面板）
```

## LaunchAgent 清单（重启 Mac 全自启）

| Label | 周期 | 职责 |
|---|---|---|
| com.kindle.panel | KeepAlive | server.py 主服务 |
| com.kindle.sensors | 30s | CPU 温度/GPU/风扇 |
| com.kindle.wx | 30min | 天气（城市在 city.txt） |
| com.kindle.alert | 5min | 24h 历史落盘 + 阈值告警推送 |

## 告警呈现

- 通知走独立 app `/Applications/Mac告警.app`（自定义仪表图标）——
  alert.sh 写 /tmp/kindle-alert-msg 后 `open -g` 唤起它发通知
- 图标源码 make-icon.py（Pillow 画），构建脚本 build-alert-app.sh
- 磁盘紧急自愈：余量 <5G 自动清自有日志并弹"磁盘紧急"通知

## 设备侧守护

- `* * * * * /mnt/us/usbnet/watchdog.sh` — v3.1：USBNet 自愈 + core dump 防再生
- cron `pgrep fetchloop || nohup …` — 拉活面板循环
- 心跳：fetchloop 每次拉取即心跳，超 5 分钟 Mac 弹"Kindle 失联"
- 屏保仪表：fetchloop v5 双拉取（面板+屏保图），md5 变化才写 rootfs；
  24h 折线数据来自 alert.sh 落盘的 history.json

## 常用命令

```bash
python3 refresh-now.py          # Mac 端改布局后立即推屏（不等 15s）
bash alert.sh                   # 手动跑一次告警判定（弹通知）
python3 deploy-fetchloop-v2.py  # 重推设备端 fetchloop（幂等）
python3 deploy-ss.py            # 重推屏保链路（幂等，含原图备份）
python3 deploy-cleanup.py       # 清理探测 / --apply 执行
```

## 屏保恢复原厂

```bash
# 设备端（telnet 192.168.15.244）：
mntroot rw && cp /mnt/us/usbnet/ss00.orig.bak /usr/share/blanket/screensaver/bg_ss00.png && mntroot ro
```

## 遥测弹窗治理（⚠️ 2026-11-14 修正）

**⛔️ 禁止把 /etc/upstart/*.conf 改名 .disabled 来禁服务**——框架启动链依赖这些
job（`start on started demd` 等事件），改名后设备卡开机转圈，powerd/appmgrd 全
不启动，只能恢复文件+重启救回（2026-11-14 实测，重启 5 次才恢复）。
`disable-logcol.py` 已改成墓碑文件防止误运行。

弹窗的正确治理（均已落地）：
1. core_pattern 改为有效管道丢弃（watchdog v3.2）
2. 面板 HTML 不含 JS（时钟 JS 已回退）
3. float 布局稳定后 mesquite 不崩溃，无崩溃即无弹窗
4. 仅 `last_debug_info.conf.disabled` 保留禁用（实测不挡启动）

## 关键坑位（详见记忆 kindle-pw4-usbnet-install-state）

- **mesquite 只可靠支持 float 布局**：flex/`-webkit-box`/table 全部会塌缩或竖排
  （2026-11-14 设备实测五种布局对比），面板 CSS 全部用 float+overflow:hidden
- mesquite 布局绘制状态坏了（卡片塌缩但值位置正确）→ **重启设备即愈**，
  eips -c 清屏重绘无效
- USB 重枚举（重启/重插）后 Mac 侧 IP 由 RNDIS/Ethernet Gadget 服务自动恢复，
  接口名会变（en5→en10），排查先看 `arp -a`
- mesquite 对内联 onclick 不可靠 → 刷新靠 meta refresh 300 兜底 + 退出重进
- /etc 只读，改 crontab 必须 mntroot rw
- U 盘模式下设备访问不到 /mnt/us → watchdog 空转，必须 diskutil eject
- mesquite 崩溃吐 core → watchdog 每分钟自动清
- telnet pty 行长 ~200 字符 → 长命令 base64 分块推送
- fetchloop 拉取覆盖 app 目录 HTML，做设备端测试前先 `pkill -f fetchloop`
