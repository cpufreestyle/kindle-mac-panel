#!/bin/bash
# 异常告警 — launchd 每 5 分钟调用：阈值判定 + macOS 通知（同类 30 分钟去重）
/usr/bin/python3 - <<'PYEOF'
import psutil, json, time, os
BASE = os.path.dirname(os.path.abspath(__file__))

# ---- 静音时段（23:00-08:00 紧急类照弹，普通类只记录不弹）----
HOUR = time.localtime().tm_hour
QUIET = HOUR >= 23 or HOUR < 8

# ---- 24h 历史落盘（每 5 分钟 1 点，保留 288 点 ≈ 24h）----
try:
    HIST = os.path.join(BASE, "history.json")
    try:
        hist = json.load(open(HIST))
    except Exception:
        hist = []
    t = None
    try:
        t = json.load(open("/tmp/kindle-sensors.json")).get("cpu_temp")
    except Exception:
        pass
    hist.append([time.strftime("%H:%M"),
                 round(t) if isinstance(t, (int, float)) else None,
                 round(psutil.cpu_percent(interval=1)),
                 round(psutil.swap_memory().used / 1024 ** 3, 1)])
    json.dump(hist[-288:], open(HIST, "w"))
except Exception:
    pass

# ---- 阈值告警 ----
SEV = []
sm = psutil.swap_memory().used / 1024 ** 3
if sm >= 4:
    SEV.append("内存压力高 %.1fG 建议重启" % sm)
cpu = psutil.cpu_percent(interval=1)
if cpu >= 80:
    SEV.append("CPU 高载 %d%%" % cpu)
du = psutil.disk_usage("/")
free = du.free / 1024 ** 3
if free <= 5:
    # 紧急自愈：清自己的日志（防 ENOSPC 波峰瘫服务），再告警
    for f in ("/tmp/kindle-panel.log", "/tmp/panel-srv.out", "/tmp/srv_dbg.out",
              "/tmp/panel_err.log", "/tmp/d.b64"):
        try:
            os.remove(f)
        except OSError:
            pass
    SEV.append("磁盘紧急 %.0fG 已自动清日志" % free)
elif free <= 20:
    SEV.append("磁盘仅剩 %.0fG" % free)
try:
    j = json.load(open("/tmp/kindle-sensors.json"))
    t = j.get("cpu_temp")
    if isinstance(t, (int, float)) and t >= 85:
        SEV.append("温度偏高 %.0f°C" % t)
    g = j.get("gpu")
    if isinstance(g, (int, float)) and g >= 70:
        SEV.append("GPU 高载 %d%%" % g)
except Exception:
    pass
b = psutil.sensors_battery()
if b and b.percent <= 20 and not b.power_plugged:
    SEV.append("Mac 电量低")
try:
    hb = float(open("/tmp/kindle-kheartbeat").read().strip())
    if time.time() - hb > 300:
        SEV.append("Kindle 失联超5分钟")
except Exception:
    SEV.append("Kindle 失联(无心跳)")

# 静音时段：普通类告警降级为只记录（紧急类：磁盘/失联/电量 仍弹）
if QUIET:
    URGENT = [s for s in SEV if s.startswith(("磁盘", "Kindle 失联", "Mac 电量低"))]
    SEV[:] = URGENT

if not SEV:
    raise SystemExit

ST = "/tmp/kindle-alert.json"
try:
    prev = json.load(open(ST))
except Exception:
    prev = {}
now = time.time()
new = {}
fired = []
for s in SEV:
    if now - prev.get(s, 0) > 1800:
        fired.append(s)
        new[s] = now
    else:
        new[s] = prev[s]
json.dump(new, open(ST, "w"), ensure_ascii=False)

if fired:
    msg = " · ".join(fired)
    with open("/tmp/kindle-alert-msg", "w") as f:
        f.write(msg)
    os.system('/usr/bin/open -g "/Applications/Mac告警.app"')
    # Bark 手机推送（配置 bark-url.txt 填入推送 URL 即激活）
    try:
        burl = open(os.path.join(BASE, "bark-url.txt")).read().strip()
        if burl.startswith("http"):
            import urllib.parse, urllib.request as _ur
            _op = _ur.build_opener(_ur.ProxyHandler({}))
            _u = burl.rstrip("/") + "/" + urllib.parse.quote("Mac 告警") + "/" + \
                urllib.parse.quote(msg) + "?group=MacMonitor"
            _op.open(_u, timeout=6)
    except Exception:
        pass
PYEOF
