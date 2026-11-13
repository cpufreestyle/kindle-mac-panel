#!/usr/bin/env python3
# Kindle 面板数据服务器 — psutil 采集 macOS 系统指标
# 端口 18866，仅局域网使用。依赖：pip3 install --user psutil
import json
import os
import socket
import time
import threading
import psutil

BASE = os.path.dirname(os.path.abspath(__file__))   # 仓库根目录（相对路径，多机复用）
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORT = 18866
NET_IFACE = "en0"
_net_prev = {"ts": 0.0, "sent": 0, "recv": 0}
HIST_CPU = []      # CPU 占用历史（60 点 ≈ 15 分钟）
HIST_MEM = []      # 内存占用历史
HIST_TEMP = []     # CPU 温度历史（None 表示该次无读数）
KINDLE_K = [""]    # 设备端 fetchloop 经 ?k= 上报的 Kindle 指标 "电池;温度m°C;内存KB;存储KB"


_PROC = {}         # pid -> (cpu_times 总和, wall 时间)，算进程 CPU 占用差分
_PSLOCK = threading.Lock()   # psutil process_iter 并发 segfault 防护（macOS 已知竞态）
_TOP_CACHE = {"ts": 0.0, "list": []}   # 60s 缓存——进程排行慢变，别每请求重算
_NET_MB = [0.0]    # 服务启动以来累计流量 MB（e-ink 显示累计而非瞬时网速）
_IO_PREV = {"ts": 0.0, "r": 0, "w": 0}   # 磁盘 IO 差分


def top_procs(n=3):
    """最耗 CPU 进程 top n（60s 缓存：进程排行慢变，瞬时快照无意义）"""
    now = time.time()
    if now - _TOP_CACHE["ts"] < 60 and _TOP_CACHE["list"]:
        return _TOP_CACHE["list"]
    with _PSLOCK:
        out = _top_procs_uncached(n)
    return out


def _top_procs_uncached(n=3):
    now = time.time()
    out = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_times']):
        try:
            ct = p.info['cpu_times']
            tot = ct.user + ct.system + getattr(ct, 'iowait', 0)
            pid = p.info['pid']
            prev = _PROC.get(pid)
            if prev and now > prev[1]:
                pct = (tot - prev[0]) / (now - prev[1]) * 100 / psutil.cpu_count()
                if pct >= 0.5:
                    out.append((p.info['name'] or '?', round(pct, 1)))
            _PROC[pid] = (tot, now)
        except Exception:
            pass
    if len(_PROC) > 1500:
        _PROC.clear()
    result = sorted(out, key=lambda x: -x[1])[:n]
    _TOP_CACHE.update(ts=now, list=result)
    return result


def collect():
    usage = round(psutil.cpu_percent(interval=0.3), 1)
    # GPU 占用现采（ioreg ~150ms，摆脱 30s sensors 缓存）
    try:
        import subprocess as _sp
        _io_out = _sp.run(["ioreg", "-r", "-c", "IOAccelerator"], capture_output=True,
                          text=True, timeout=3).stdout
        import re as _re
        _m = _re.search(r'"Device Utilization %"=(\d+)', _io_out)
        gpu_live = int(_m.group(1)) if _m else None
    except Exception:
        gpu_live = None
    HIST_CPU.append(usage)
    if len(HIST_CPU) > 60:
        HIST_CPU.pop(0)

    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    du = psutil.disk_usage("/System/Volumes/Data")   # 真实数据卷（/ 是系统快照假象）

    batt = psutil.sensors_battery()
    batt_out = None
    if batt:
        left = batt.secsleft if isinstance(batt.secsleft, int) and batt.secsleft > 0 else None
        batt_out = {"percent": batt.percent, "charging": bool(batt.power_plugged), "left": left}

    io = psutil.disk_io_counters()
    io_out = None
    if io:
        now = time.time()
        if _IO_PREV["ts"] and now - _IO_PREV["ts"] > 5:
            dt = now - _IO_PREV["ts"]
            io_out = {"rMBs": round(max(0, io.read_bytes - _IO_PREV["r"]) / dt / 1048576, 1),
                      "wMBs": round(max(0, io.write_bytes - _IO_PREV["w"]) / dt / 1048576, 1)}
        _IO_PREV.update(ts=now, r=io.read_bytes, w=io.write_bytes)

    nio = psutil.net_io_counters(pernic=True).get(NET_IFACE)
    net_out = None
    if nio:
        now = time.time()
        r = {"downKBs": 0.0, "upKBs": 0.0}
        if _net_prev["ts"] and now - _net_prev["ts"] > 0.5:
            dr = max(0, nio.bytes_recv - _net_prev["recv"])
            ds = max(0, nio.bytes_sent - _net_prev["sent"])
            _NET_MB[0] += (dr + ds) / 1048576.0
            r["totalMB"] = round(_NET_MB[0], 1)
        _net_prev.update(ts=now, recv=nio.bytes_recv, sent=nio.bytes_sent)
        net_out = r

    l1, l5, l15 = psutil.getloadavg()
    boot = psutil.boot_time()
    up = int(time.time() - boot)
    days, rem = divmod(up, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    uptime = ("%dd %dh %dm" % (days, hours, minutes)) if days else ("%dh %dm" % (hours, minutes))

    sensors = {}
    try:
        with open("/tmp/kindle-sensors.json") as f:
            sensors = json.load(f)
    except Exception:
        pass
    for k in ("pstr", "avg_pstr", "tb1t", "th0x", "tw0p", "id0r", "vd0r"):
        if not isinstance(sensors.get(k), (int, float)):
            sensors[k] = None

    HIST_MEM.append(round(vm.percent, 1))
    if len(HIST_MEM) > 60:
        HIST_MEM.pop(0)
    _t = sensors.get("cpu_temp")
    HIST_TEMP.append(_t if isinstance(_t, (int, float)) else None)
    if len(HIST_TEMP) > 60:
        HIST_TEMP.pop(0)

    swap_used = sm.used / 1024 ** 3
    avg15 = round(sum(HIST_CPU) / len(HIST_CPU), 1) if len(HIST_CPU) >= 3 else usage

    return {
        "ts": time.strftime("%H:%M:%S"),
        "cpu": {"usage": usage, "avg15": avg15, "temp": sensors.get("cpu_temp")},
        "mem": {
            "usedGB": round(vm.used / 1024 ** 3, 1),
            "totalGB": round(vm.total / 1024 ** 3, 1),
            "usage": round(vm.percent, 1),
        },
            "swap": {"usedGB": round(sm.used / 1024 ** 3, 1)},
        "disk": {
            "usedGB": round(du.used / 1024 ** 3, 1),
            "freeGB": round(du.free / 1024 ** 3, 0),
            "totalGB": round(du.total / 1024 ** 3, 1),
            "usage": round(du.percent, 1),
        },
        "batt": batt_out,
        "io": io_out,
        "gpu": gpu_live if gpu_live is not None else sensors.get("gpu"),
        "fan": sensors.get("fan"),
        "pstr": sensors.get("pstr"),
        "avg_pstr": sensors.get("avg_pstr"),
        "tb1t": sensors.get("tb1t"),
        "th0x": sensors.get("th0x"),
        "tw0p": sensors.get("tw0p"),
        "id0r": sensors.get("id0r"),
        "vd0r": sensors.get("vd0r"),
        "net": net_out,
        "load": {"l1": round(l1, 2), "l5": round(l5, 2), "l15": round(l15, 2)},
        "uptime": uptime,
    }


_WX = {"txt": "", "range": "", "sun": ""}   # 天气（wx.sh launchd 每 30 分钟写 /tmp/kindle-wx.json）


def _wx_read():
    try:
        with open("/tmp/kindle-wx.json") as f:
            w = json.load(f)
            _WX["txt"] = w.get("txt", "")
            _WX["range"] = w.get("range", "")
            _WX["sun"] = w.get("sun", "")
    except Exception:
        _WX["txt"] = ""
        _WX["range"] = ""
        _WX["sun"] = ""


PANEL_CSS = """
html, body { margin:0; padding:0; background:#fff; color:#000; height:100%; width:100%;
  overflow:hidden; font-family:"Microsoft YaHei","Noto Sans CJK",sans-serif; }
#app { min-height:100%; box-sizing:border-box; padding:20px 36px 14px 36px; }
#head { overflow:hidden; border-bottom:4px solid #000; padding-bottom:8px; }
#title { float:left; font-size:46px; font-weight:bold; letter-spacing:0.12em; }
#ts { float:right; font-size:30px; color:#333; font-weight:bold; }
.card { border:3px solid #000; padding:10px 16px 12px 16px; margin-bottom:12px; }
.sech { font-size:30px; font-weight:bold; margin-bottom:4px; }
.row { overflow:hidden; margin-top:8px; }
.label { float:left; width:110px; font-size:30px; font-weight:bold; }
.bar { overflow:hidden; height:36px; background:#fff; border:3px solid #000;
  margin:0 14px; }
.fill { height:100%; background:#000; }
.val { float:right; width:150px; text-align:right; font-size:30px; font-weight:bold; }
.dt { font-size:21px; color:#444; margin-top:6px; }
.kb { margin-top:10px; }
.kbl { overflow:hidden; width:100%; font-size:30px; font-weight:bold; }
.kbl span:first-child { float:left; }
.kbl span:last-child { float:right; }
.kbt { margin-top:6px; height:36px; border:3px solid #000;
  background:#fff; overflow:hidden; }
.kbf { height:100%; background:#000; }
.kline { margin-top:8px; font-size:23px; color:#444; }
#advice { font-size:23px; color:#444; margin-top:10px; }
"""


_COND = {}   # 建议防抖：条件 -> 连续命中次数（fetchloop 15s 一采样，≥3 次≈45s 确认）

LAYTEST_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>LAY</title><style>
body { margin:0; background:#fff; font-family:sans-serif; }
.b { border:3px solid #000; margin:10px 20px; padding:6px 16px; }
h3 { margin:4px 0; font-size:24px; }
.v1 .bar { display:block; height:28px; border:3px solid #000; margin:4px 0; }
.v2 .tr { display:table-row; }
.v2 .c { display:table-cell; font-size:24px; padding:0 8px; }
.v2 .bar { display:table-cell; height:28px; border:3px solid #000; }
.v3 .r { display:-webkit-box; -webkit-box-orient:horizontal; }
.v3 .l { width:110px; font-size:24px; }
.v3 .bar { -webkit-box-flex:1; height:28px; border:3px solid #000; margin:0 10px; }
.v3 .v { width:130px; font-size:24px; text-align:right; }
.v4 .r { display:flex; align-items:center; }
.v4 .l { width:110px; font-size:24px; }
.v4 .bar { flex:1; height:28px; border:3px solid #000; margin:0 10px; }
.v4 .v { width:130px; font-size:24px; text-align:right; }
.v6 { overflow:hidden; }
.v6 .l { float:left; width:110px; font-size:24px; }
.v6 .bar { overflow:hidden; height:28px; border:3px solid #000; margin:0 10px; }
.v6 .v { float:right; width:130px; font-size:24px; text-align:right; }
</style></head><body>
<div class="b v1"><h3>V1 BLOCK</h3>
<div><div class="l">L1</div><div class="bar" style="width:55%"></div></div>
<div><div class="l">L2</div><div class="bar" style="width:75%"></div></div></div>
<div class="b v2"><h3>V2 TABLE</h3>
<div class="tr"><div class="c">L1</div><div class="bar" style="width:55%"></div><div class="c">V1</div></div>
<div class="tr"><div class="c">L2</div><div class="bar" style="width:75%"></div><div class="c">V2</div></div></div>
<div class="b v3"><h3>V3 WEBKITBOX</h3>
<div class="r"><div class="l">L1</div><div class="bar" style="width:55%"></div><div class="v">V1</div></div>
<div class="r"><div class="l">L2</div><div class="bar" style="width:75%"></div><div class="v">V2</div></div></div>
<div class="b v4"><h3>V4 FLEX</h3>
<div class="r"><div class="l">L1</div><div class="bar" style="width:55%"></div><div class="v">V1</div></div>
<div class="r"><div class="l">L2</div><div class="bar" style="width:75%"></div><div class="v">V2</div></div></div>
<div class="b v6"><h3>V6 FLOAT</h3>
<div class="r"><div class="l">L1</div><div class="bar" style="width:55%"></div><div class="v">V1</div></div>
<div class="r"><div class="l">L2</div><div class="bar" style="width:75%"></div><div class="v">V2</div></div></div>
</body></html>"""


def _bump(key, hit):
    """连续 3 次为真才触发（冷启动首采直接确认）；为假渐退（滞回防闪烁）"""
    if key not in _COND:
        _COND[key] = 3 if hit else 0
        return hit
    n = _COND[key]
    n = n + 1 if hit else max(0, n - 1)
    _COND[key] = n
    return n >= 3


def _advice(d, kparts):
    """规则引擎：当前数据 → 人话建议（防抖后按严重度取前 2 条）"""
    tips = []
    swp = (d.get("swap") or {}).get("usedGB") or 0
    if _bump("swap_hi", swp >= 4):
        tips.append("内存压力高(%sG) 建议重启 Mac" % swp)
    elif _bump("swap_mid", swp >= 2):
        tips.append("内存有压力(%sG)" % swp)
    cpu = (d.get("cpu") or {}).get("avg15") or 0
    if _bump("cpu_hi", cpu >= 70):
        tips.append("CPU 高载(%d%%) 查后台程序" % cpu)
    free = (d.get("disk") or {}).get("freeGB")
    if _bump("disk_lo", free is not None and free <= 20):
        tips.append("磁盘仅剩%dG 建议清理" % free)
    temp = (d.get("cpu") or {}).get("temp")
    if _bump("temp_hi", isinstance(temp, (int, float)) and temp >= 85):
        tips.append("温度偏高(%d°C)" % temp)
    gpu = d.get("gpu")
    if _bump("gpu_hi", isinstance(gpu, (int, float)) and gpu >= 70):
        tips.append("GPU 高载(%d%%) 视频/游戏/外接显示?" % gpu)
    batt = d.get("batt")
    if _bump("batt_lo", bool(batt) and batt["percent"] <= 20 and not batt["charging"]):
        tips.append("Mac 电量低")
    tb1 = d.get("tb1t")
    if _bump("tb1t_hi", isinstance(tb1, (int, float)) and tb1 >= 45):
        tips.append("电池温度高(%d°C)" % tb1)
    ps = d.get("pstr")
    if _bump("pstr_hi", isinstance(ps, (int, float)) and ps >= 80):
        tips.append("功耗异常(%dW)" % ps)
    kb_hit = False
    try:
        kb_hit = bool(kparts and kparts[0] and float(kparts[0]) <= 20)
    except (TypeError, ValueError):
        pass
    if _bump("kb_batt_lo", kb_hit):
        tips.append("Kindle 该充电了")
    return tips[:2] or ["一切正常"]


def ring_datauri(pct):
    """环形图 → base64 data URI（mesquite 不加载相对路径 img）"""
    import base64
    import io as _io
    buf = _io.BytesIO()
    render_ring(pct).save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def render_ring(pct, size=220):
    """Stats 风格环形仪表（黑白 e-ink）：底环浅灰 + 进度黑弧 + 中心大数字"""
    from PIL import Image, ImageDraw, ImageFont
    pct = max(0, min(100, pct or 0))
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    dr = ImageDraw.Draw(img)
    w = max(6, int(size * 0.085))
    bbox = [w, w, size - w, size - w]
    dr.arc(bbox, 0, 360, fill=(215, 215, 215, 255), width=w)
    dr.arc(bbox, -90, -90 + int(360 * pct / 100), fill=(0, 0, 0, 255), width=w)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", int(size * 0.24))
    except Exception:
        f = None
    txt = "%d%%" % round(pct)
    bb = dr.textbbox((0, 0), txt, font=f)
    dr.text(((size - bb[2] + bb[0]) / 2 - bb[0], (size - bb[3] + bb[1]) / 2 - bb[1]),
            txt, font=f, fill=(0, 0, 0, 255))
    return img


_RING_DIR = "/tmp/kindle-rings"


def ring_file(name, pct):
    """生成/刷新环形图 PNG，返回文件路径"""
    os.makedirs(_RING_DIR, exist_ok=True)
    p = os.path.join(_RING_DIR, "ring_%s.png" % name)
    render_ring(pct).convert("RGB").save(p, "PNG", optimize=True)
    return p


def render_history():
    """Mac 浏览器查看的 24h 历史页（SVG 三线：CPU/温度/Swap + 峰值表）"""
    hist = []
    try:
        hist = json.load(open(os.path.join(BASE, "history.json")))
    except Exception:
        pass
    W, H = 920, 320
    n = max(len(hist) - 1, 1)

    def pts(idx, vmax):
        out = []
        for i, h in enumerate(hist):
            if len(h) > idx and h[idx] is not None:
                out.append("%d,%d" % (60 + i / n * (W - 100),
                                      H - 60 - min(h[idx], vmax) / vmax * (H - 100)))
        return " ".join(out)

    svg = ('<svg width="%d" height="%d" xmlns="http://www.w3.org/2000/svg">'
           '<rect width="100%%" height="100%%" fill="white"/>'
           '<line x1="60" y1="%d" x2="%d" y2="%d" stroke="#999" stroke-width="2"/>'
           '<line x1="60" y1="%d" x2="%d" y2="%d" stroke="#ccc" stroke-width="1"/>'
           '<polyline points="%s" fill="none" stroke="#000" stroke-width="3"/>'
           '<polyline points="%s" fill="none" stroke="#888" stroke-width="2"/>'
           '<polyline points="%s" fill="none" stroke="#bbb" stroke-width="2"/>'
           '</svg>') % (
        W, H, H - 60, W - 40, H - 60,
        H - 60 - (H - 100) // 2, W - 40, H - 60 - (H - 100) // 2,
        pts(2, 100), pts(1, 100), pts(3, 32))
    tt = [(h[0], h[1]) for h in hist if len(h) > 1 and h[1] is not None]
    cc = [(h[0], h[2]) for h in hist if len(h) > 2 and h[2] is not None]
    rows = ""
    if cc:
        cm = max(cc, key=lambda x: x[1])
        rows += ('<tr><td>CPU 峰值</td><td>%d%% @ %s</td></tr>' % (cm[1], cm[0]))
    if tt:
        tm = max(tt, key=lambda x: x[1])
        rows += ('<tr><td>温度峰值</td><td>%d°C @ %s</td></tr>' % (tm[1], tm[0]))
    rows += ('<tr><td>样本</td><td>%d 点（5 分钟/点，约 24h）</td></tr>' % len(hist))
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Mac 24h 历史</title>'
            '<style>body{font-family:"Microsoft YaHei",sans-serif;margin:30px;color:#000}'
            'table{border-collapse:collapse;margin-top:16px}td{border:1px solid #999;'
            'padding:6px 18px}h2{margin-bottom:4px}.lg span{margin-right:24px}</style></head><body>'
            '<h2>Mac 24 小时历史</h2>'
            '<div class="lg"><span>━ CPU%（黑）</span><span>━ 温度°C（深灰）</span>'
            '<span>━ Swap G（浅灰，满量程 32G）</span></div>' + svg +
            '<table>' + rows + '</table><p>每 5 分钟采样 · refresh-now 可手动刷新数据</p></body></html>')


def render_panel():
    d = collect()
    _wx_read()
    cpu = d.get("cpu") or {"usage": 0}
    mem = d.get("mem") or {"usedGB": 0, "totalGB": 0, "usage": 0}
    disk = d.get("disk") or {"usage": 0}
    batt = d.get("batt")
    net = d.get("net") or {"downKBs": 0, "upKBs": 0}
    load = d.get("load") or {"l1": 0}
    gpu = d.get("gpu")
    fan = d.get("fan")
    temp = cpu.get("temp")
    batt_s = ("%d%%⚡" % batt["percent"]) if (batt and batt["charging"]) else (
        ("%d%%" % batt["percent"]) if batt else "--")
    temp_s = ("%.0f°C" % temp) if isinstance(temp, (int, float)) else "--"
    gpu_s = ("%d%%" % gpu) if isinstance(gpu, (int, float)) else "--"
    fan_s = ("%d rpm" % fan) if isinstance(fan, (int, float)) else "--"
    cpu_show = cpu.get("avg15") or cpu.get("usage") or 0
    swap = d.get("swap") or {}
    swap_gb = swap.get("usedGB") or 0
    swap_s = (str(swap.get("usedGB")) + "G") if swap.get("usedGB") is not None else "--"
    disk = d.get("disk") or {}
    disk_free_s = (str(int(disk.get("freeGB", 0))) + "G") if disk.get("freeGB") is not None else "--"

    # Mac Wi-Fi IP（SSH/ScreenControl 连接用，开发者高频刚需）
    mac_ip = "--"
    try:
        for a in psutil.net_if_addrs().get(NET_IFACE) or []:
            if a.family == socket.AF_INET and not a.address.startswith("169.254"):
                mac_ip = a.address
                break
    except Exception:
        pass

    # 实时指标档位化：状态词不过时，数字退居趋势图/屏保
    def _lv(v, bands):
        for lim, word in bands:
            if v < lim:
                return word
        return bands[-1][1]
    cpu_lv = _lv(cpu_show, [(25, "空闲"), (60, "正常"), (85, "忙碌"), (999, "满载")])
    gpu_v = d.get("gpu")
    gpu_show = gpu_v if isinstance(gpu_v, (int, float)) else 0
    gpu_lv = _lv(gpu_show, [(25, "空闲"), (60, "正常"), (85, "忙碌"), (999, "满载")])
    ld, nt, io2 = d.get("load") or {}, d.get("net") or {}, d.get("io") or {}
    l1v = ld.get("l1") or 0
    load_lv = _lv(l1v, [(3, "正常"), (8, "偏高"), (999, "过载")])
    netv = (nt.get("downKBs") or 0) + (nt.get("upKBs") or 0)
    net_lv = _lv(netv, [(10, "空闲"), (200, "一般"), (9999, "活跃")])
    io_r = io2.get("rMBs") or 0
    io_w = io2.get("wMBs") or 0
    io_lv = _lv(io_r + io_w, [(1, "空闲"), (20, "中等"), (9999, "繁忙")])
    fan = d.get("fan")
    fan_lv = _lv(fan or 0, [(2200, "静音"), (3500, "常速"), (9999, "高速")]) if isinstance(fan, (int, float)) else "--"

    # 趋势迷你柱状图 — 墨水屏零灰度形态区分：实心/空心/细柱 + 峰值柱加宽高亮
    def spark_row(label, vals, vmax, style="solid"):
        bars = []
        vals_n = [v if v is not None else -1 for v in vals]
        if len([v for v in vals if v is not None]) >= 2:
            step = 100.0 / 60
            mx = max(range(len(vals_n)), key=lambda i: vals_n[i])
            for i, v in enumerate(vals):
                if v is None:
                    continue
                hpx = max(3, int(min(v, vmax) / vmax * 56))
                wpx = 2.4 if i == mx else 1.4  # 峰值柱加宽
                if style == "solid":
                    st = ('position:absolute;left:%.2f%%;bottom:0;width:%s%%;'
                          'height:%dpx;background:#000' % (i * step, wpx, hpx))
                elif style == "hollow":
                    st = ('position:absolute;left:%.2f%%;bottom:0;width:%s%%;'
                          'height:%dpx;border:2px solid #000;box-sizing:border-box'
                          % (i * step, wpx, hpx))
                else:  # thin
                    st = ('position:absolute;left:%.2f%%;bottom:0;width:0.7%%;'
                          'height:%dpx;background:#000' % (i * step, hpx))
                bars.append('<div style="' + st + '"></div>')
        if not bars:
            return ""
        return ('<div class="srow"><div class="slabel">' + label + "</div>"
                '<div class="sbox">' + "".join(bars) + "</div></div>")

    hist_html = ""
    _rows = [spark_row("CPU", HIST_CPU, 100, "solid")]
    if any(_rows):
        hist_html = ('<div id="spark">' + "".join(r for r in _rows if r) + "</div>")

    # Kindle 指标（fetchloop 经 ?k=电池;温度m°C;内存KB;存储KB 上报）
    kparts = (KINDLE_K[0].split(";") + ["", "", "", "", ""])[:5]
    kb, kt, km, kd, kcs = kparts
    def _num(x, conv):
        try:
            return conv(float(x))
        except (TypeError, ValueError):
            return None
    kb_s = (_num(kb, lambda v: "%d%%" % v)) if kb else "--"
    if kb_s != "--" and kcs == "1":
        kb_s += "⚡"
    kt_s = (_num(kt, lambda v: "%.1f°C" % (v / 1000.0))) if kt else "--"
    km_s = (_num(km, lambda v: "%d%%" % v)) if km else "--"
    kd_s = (_num(kd, lambda v: "%d%%" % v)) if kd else "--"
    advice = " · ".join(_advice(d, kparts))
    # 供电/传感器行（第一二梯队 SMC）
    _sx = lambda k: d.get(k)
    pstr = _sx("pstr")
    aps = _sx("avg_pstr")
    pstr_s = ("%.1fW" % pstr) if isinstance(pstr, (int, float)) else "--"
    aps_s = ("%.1fW" % aps) if isinstance(aps, (int, float)) else "--"
    vd0r = _sx("vd0r")
    id0r = _sx("id0r")
    pwr_s = ("%.1fV/%.2fA" % (vd0r, id0r)) if isinstance(vd0r, (int, float)) and isinstance(id0r, (int, float)) else "--"
    tb1t = _sx("tb1t")
    tb1t_s = ("%.0f°" % tb1t) if isinstance(tb1t, (int, float)) else "--"
    metrics_s = ("功耗 %s（均 %s）· 供电 %s · 电池 %s" % (pstr_s, aps_s, pwr_s, tb1t_s))
    ld, nt, io2 = d.get("load") or {}, d.get("net") or {}, d.get("io") or {}
    # 右栏环境/历史块
    bl = d.get("batt") or {}
    dur = "充电中" if bl.get("charging") else (
        "续航 %dh%02dm" % (bl["left"] // 3600, bl["left"] % 3600 // 60) if bl.get("left") else "")
    meta1 = " · ".join(x for x in (_WX["sun"], dur) if x)
    peak24 = ""
    try:
        hist = json.load(open(os.path.join(BASE, "history.json")))
        tv = [h[1] for h in hist if len(h) > 1 and h[1] is not None]
        cv = [h[2] for h in hist if len(h) > 2 and h[2] is not None]
        pp = []
        if cv:
            cm = max(cv)
            pp.append("CPU %d%%" % cm)
        if tv:
            tm = max(tv)
            pp.append("温 %d°" % tm)
        peak24 = " · ".join(pp)
    except Exception:
        pass
    tp = top_procs()
    tp_s = " · ".join(nm[:10] for nm, v in tp)   # 只列进程名，实时百分比已删
    avg15 = cpu.get("avg15") or cpu_show
    free_gb = (mem.get("totalGB") or 0) - (mem.get("usedGB") or 0)
    used_gb = mem.get("usedGB") or 0
    disk_used = disk.get("usedGB") or 0
    fan_v = d.get("f0ac")
    tw0p_v = d.get("tw0p")
    th0x_v = d.get("th0x")
    tb1t_v = d.get("tb1t")
    pstr_v = d.get("pstr")
    aps_v = d.get("avg_pstr")
    def fnum(x, fmt):
        return (fmt % x) if isinstance(x, (int, float)) else "--"
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="300">'
        "<title>Mac 面板</title><style>" + PANEL_CSS + "</style></head>"
        '<body><div id="app">'
        '<div id="head"><span id="title">MAC 面板</span>'
        '<span id="ts">' + d["ts"] + ((" · " + _WX["txt"]) if _WX["txt"] else "") + "</span></div>"
        # Stats 式分节（全部块级 + 已验证单层 flex 行）
        '<div class="card"><div class="sech">CPU</div>'
        '<div class="row"><div class="label">占用</div>'
        '<div class="val">' + cpu_lv + '</div>'
        '<div class="bar"><div class="fill" style="width:' + str(cpu_show) + '%"></div></div></div>'
        '<div class="row"><div class="label">GPU</div>'
        '<div class="val">' + gpu_lv + '</div>'
        '<div class="bar"><div class="fill" style="width:' + str(gpu_show) + '%"></div></div></div>'
        '<div class="dt">15分均 ' + str(avg15) + '% · 最耗 ' + tp_s + '</div></div>'
        '<div class="card"><div class="sech">RAM</div>'
        '<div class="row"><div class="label">内存</div>'
        '<div class="val">' + str(mem["usage"]) + '%</div>'
        '<div class="bar"><div class="fill" style="width:' + str(mem["usage"]) + '%"></div></div></div>'
        '<div class="row"><div class="label">Swap</div>'
        '<div class="val">' + swap_s + '</div>'
        '<div class="bar"><div class="fill" style="width:'
        + str(min(int(swap_gb / 16.0 * 100), 100)) + '%"></div></div></div>'
        '<div class="card"><div class="sech">Disk</div>'
        '<div class="row"><div class="label">磁盘</div>'
        '<div class="val">' + str(disk["usage"]) + '%</div>'
        '<div class="bar"><div class="fill" style="width:' + str(disk["usage"]) + '%"></div></div></div>'
        '<div class="dt">已用 ' + fnum(disk_used, "%.0fG") + ' · 剩余 ' + disk_free_s + ' · IP ' + mac_ip + '</div></div>'
        '<div class="card"><div class="sech">KINDLE</div>'
        '<div class="kb"><div class="kbl"><span>电池</span><span>' + kb_s + '</span></div>'
        '<div class="kbt"><div class="kbf" style="width:' + (kb or "0") + '%"></div></div></div>'
        '<div class="kb"><div class="kbl"><span>内存</span><span>' + km_s + '</span></div>'
        '<div class="kbt"><div class="kbf" style="width:' + (km or "0") + '%"></div></div></div>'
        '<div class="kb"><div class="kbl"><span>存储</span><span>' + kd_s + '</span></div>'
        '<div class="kbt"><div class="kbf" style="width:' + (kd or "0") + '%"></div></div></div>'
        '<div class="kline">温度 ' + kt_s + " · " + (_WX["sun"] or " ") + " · 24h峰 " + (peak24 or "积累中") + '</div>'
        '</div>'
        '<div class="card"><div class="sech">Sensors</div>'
        '<div class="kline">风扇 ' + fnum(fan_v, "%.0f") + ' rpm · Airport ' + fnum(tw0p_v, "%.1f°C") + ' · NAND ' + fnum(th0x_v, "%.1f°C") + '</div>'
        '<div class="kline">电池 ' + fnum(tb1t_v, "%.1f°C") + ' · 功耗 ' + fnum(pstr_v, "%.1fW") + '（均 ' + fnum(aps_v, "%.1fW") + '）· 供电 ' + pwr_s + '</div>'
        '</div>'
        '<div id="advice">' + advice + ' · 已运行 ' + d["uptime"] + '</div>'
        "</div></body></html>"
    )


def render_screensaver():
    """屏保仪表图 1072×1448 白底黑字（e-ink 零功耗常显）"""
    from PIL import Image, ImageDraw, ImageFont
    d = collect()
    _wx_read()
    kparts = (KINDLE_K[0].split(";") + ["", "", "", "", ""])[:5]
    kb, kt, km, kd, kcs = kparts
    def _n(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    W, H = 1072, 1448
    img = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(img)
    FP = "/System/Library/Fonts/STHeiti Medium.ttc"
    def F(sz):
        return ImageFont.truetype(FP, sz)
    def line(y, txt, sz, gray=0):
        dr.text((60, y), txt, font=F(sz), fill=gray)
    def bar(y, w_ratio, label, val):
        dr.text((60, y), label, font=F(52), fill=0)
        bx, bw, bh = 260, 600, 44
        dr.rectangle([bx, y + 8, bx + bw, y + 8 + bh], outline=0, width=4)
        if w_ratio > 0:
            dr.rectangle([bx, y + 8, bx + int(bw * min(w_ratio, 100) / 100), y + 8 + bh], fill=0)
        dr.text((890, y), val, font=F(52), fill=0)

    # 大字时钟（左列）+ 建议卡（右上列）
    import time as _t
    dr.text((60, 90), _t.strftime("%H:%M"), font=F(230), fill=0)
    tips = _advice(d, kparts)
    bx, by, bw, bh = 610, 100, 402, 235
    dr.rectangle([bx, by, bx + bw, by + bh], outline=0, width=4)
    dr.text((bx + 22, by + 14), "建议", font=F(30), fill=90)
    ty = by + 58
    for t in tips:
        dr.text((bx + 22, ty), "▸ " + t[:14], font=F(32), fill=0)
        if len(t) > 14:
            dr.text((bx + 44, ty + 42), t[14:29], font=F(32), fill=0)
            ty += 42
        ty += 48
    dr.text((60, 360), _t.strftime("%Y-%m-%d %A"), font=F(46), fill=90)
    if _WX["txt"]:
        dr.text((700, 360), _WX["txt"], font=F(46), fill=90)

    dr.line([60, 470, W - 60, 470], fill=0, width=5)

    # MAC 区
    dr.text((60, 500), "MAC", font=F(40), fill=120)
    mem, disk = d["mem"], d["disk"]
    swp_gb = d.get("swap", {}).get("usedGB", 0)
    bar(530, mem["usage"], "内存", "%d%%" % mem["usage"])
    bar(615, swp_gb / 16.0 * 100, "Swap", "%.1fG" % swp_gb)    # 16G 满量程
    bar(700, disk["usage"], "磁盘", "%d%%" % d["disk"]["usage"])
    tp = top_procs()
    io = d.get("io") or {}
    io_s = (" · IO 读%.1f 写%.1fM/s" % (io["rMBs"], io["wMBs"])) if io else ""
    tps = " · ".join("%s %s%%" % (n[:8], v) for n, v in tp[:2]) if tp else "--"
    dr.text((60, 805), "最耗 " + tps + io_s, font=F(36), fill=90)
    l1, l5, l15 = d.get("load", {}).get("l1", 0), d.get("load", {}).get("l5", 0), d.get("load", {}).get("l15", 0)
    netd = d.get("net", {}).get("downKBs", 0)
    netu = d.get("net", {}).get("upKBs", 0)
    g = d.get("gpu")
    gs = ("%d%%" % g) if isinstance(g, (int, float)) else "--"
    dr.text((60, 770), "负载 %.1f/%.1f/%.1f · GPU %s · 网速 %.0f↓%.0f↑K/s" % (l1, l5, l15, gs, netd, netu),
            font=F(36), fill=90)

    dr.line([60, 850, W - 60, 850], fill=0, width=3)

    # KINDLE 区

    # KINDLE 区
    kbat = ("电池 %s%%%s" % (_n(kb), "⚡" if kcs == "1" else "")) if _n(kb) is not None else "电池 --"
    items = [kbat,
             ("温度 %.0f°C" % (_n(kt) / 1000) if _n(kt) is not None else "温度 --"),
             ("内存 %d%%" % _n(km) if _n(km) is not None else "内存 --"),
             ("存储 %d%%" % _n(kd) if _n(kd) is not None else "存储 --")]
    dr.text((60, 880), "KINDLE", font=F(40), fill=120)
    for i, s in enumerate(items):
        dr.text((60 + i * 245, 928), s, font=F(46), fill=0)

    dr.line([60, 1000, W - 60, 1000], fill=0, width=3)

    # 24 小时趋势折线（alert.sh 每 5 分钟落盘 history.json）
    hist = []
    try:
        hist = json.load(open(os.path.join(BASE, "history.json")))
    except Exception:
        pass
    peak_s = "峰值积累中"
    if len(hist) >= 2:
        GX, GY, GW, GH = 60, 1100, 680, 230   # 图区（右侧留 24h 统计列）
        tt = [(h[0], h[1]) for h in hist if len(h) > 1 and h[1] is not None]
        cc = [(h[0], h[2]) for h in hist if len(h) > 2 and h[2] is not None]
        n = max(len(hist) - 1, 1)
        def pt(i, v):
            return (GX + i / n * GW, GY + GH - min(v, 100) / 100.0 * GH)
        # 网格基线
        dr.line([GX, GY + GH, GX + GW, GY + GH], fill=0, width=3)
        for frac in (0.5,):
            dr.line([GX, GY + GH * (1 - frac), GX + GW, GY + GH * (1 - frac)], fill=180, width=1)
        if len(cc) >= 2:
            dr.line([pt(i, v[2]) for i, v in enumerate(hist)
                     if len(v) > 2 and v[2] is not None], fill=0, width=4, joint="curve")
        if len(tt) >= 2:
            dr.line([pt(i, v[1]) for i, v in enumerate(hist)
                     if len(v) > 1 and v[1] is not None], fill=90, width=3, joint="curve")
        sv = [(i, h[3]) for i, h in enumerate(hist) if len(h) > 3 and h[3] is not None]
        if len(sv) >= 2:
            def pt2(i, v):   # Swap 0-32G 满量程，细灰线
                return (GX + i / n * GW, GY + GH - min(v, 32) / 32.0 * GH)
            dr.line([pt2(i, v) for i, v in sv], fill=150, width=2, joint="curve")
        tm = max([x for x in tt], key=lambda x: x[1]) if tt else None
        cm = max([x for x in cc], key=lambda x: x[1]) if cc else None
        pp = []
        if cm:
            pp.append("CPU %d%%@%s" % (cm[1], cm[0]))
        if tm:
            pp.append("温 %d°@%s" % (tm[1], tm[0]))
        peak_s = "峰值 " + " · ".join(pp) if pp else peak_s
    dr.text((60, 1045), "24 小时趋势", font=F(40), fill=0)

    # 右列：24h 统计（均值/峰值）
    if len(hist) >= 2:
        tv = [h[1] for h in hist if len(h) > 1 and h[1] is not None]
        cv = [h[2] for h in hist if len(h) > 2 and h[2] is not None]
        dr.text((790, 1045), "24h 统计", font=F(30), fill=90)
        if cv:
            dr.text((790, 1090), "CPU %d%%" % (sum(cv) / len(cv)), font=F(40), fill=0)
        if tv:
            dr.text((790, 1150), "温 %d°" % (sum(tv) / len(tv)), font=F(40), fill=0)
        if len(hist) >= 2:
            dr.text((790, 1210), peak_s[:22], font=F(28), fill=120)
            if len(peak_s) > 22:
                dr.text((790, 1250), peak_s[22:44], font=F(28), fill=120)

    swp = d.get("swap", {}).get("usedGB", 0)
    drg = d.get("disk", {}).get("freeGB", 0)
    bl = d.get("batt") or {}
    dur = ""
    if bl.get("charging"):
        dur = "充电中"
    elif bl.get("left"):
        dur = "续航 %dh%02dm" % (bl["left"] // 3600, bl["left"] % 3600 // 60)
    meta1 = " · ".join(x for x in (_WX["sun"], dur) if x)
    dr.text((60, 1360), meta1 if meta1 else " ", font=F(38), fill=90)
    dr.text((60, 1412), "Swap " + str(swp) + "G · 磁盘剩 " + str(int(drg)) + "G · 今日 " + (_WX["range"] or "--") + " · " + d["ts"], font=F(36), fill=90)
    return img


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode())

    def _send_bytes(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        self.do_POST()

    def do_POST(self):
        # Kindle framebuffer 上传（诊断截图用）
        if self.path.startswith("/fb0"):
            try:
                ln = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(ln)
                with open("/tmp/fb.raw", "wb") as f:
                    f.write(data)
                self._send_bytes(200, b"OK", "text/plain")
            except Exception:
                self._send(500, b"ERR", "text/plain")
            return
        self._send(404, b"NF", "text/plain")

    def do_GET(self):
        if self.path.startswith("/favicon"):
            self.send_response(404)
            self.end_headers()
            return
        p, _, qs = self.path.partition("?")
        if qs.startswith("k="):
            KINDLE_K[0] = qs[2:]
            try:  # 设备心跳：fetchloop 每次上报即活着（告警脚本读此文件判断失联）
                with open("/tmp/kindle-kheartbeat", "w") as f:
                    f.write(str(time.time()))
            except OSError:
                pass
        if p.startswith("/ring/"):
            nm = p.split("/ring/")[1].split(".")[0]
            d2 = collect()
            m = {"cpu": (d2.get("cpu") or {}).get("usage"),
                 "gpu": d2.get("gpu"),
                 "ram": (d2.get("mem") or {}).get("usage"),
                 "disk": (d2.get("disk") or {}).get("usage")}
            import io as _io
            buf = _io.BytesIO()
            render_ring(m.get(nm) or 0).convert("RGB").save(buf, "PNG", optimize=True)
            self._send_bytes(200, buf.getvalue(), "image/png")
            return
        if p.startswith("/laytest"):
            self._send(200, LAYTEST_HTML, "text/html; charset=utf-8")
            return
        if p.startswith("/history"):
            html = render_history()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if p.startswith("/panel.html"):
            try:
                self._send(200, render_panel(), "text/html; charset=utf-8")
            except Exception:
                import traceback
                with open("/tmp/panel_err.log", "a") as f:
                    f.write(time.strftime("%H:%M:%S ") + traceback.format_exc() + "\n")
                self._send(500, "ERR", "text/plain")
            return
        if p.startswith("/screensaver.png"):
            img = render_screensaver()
            import io
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            self._send_bytes(200, buf.getvalue(), "image/png")
            return
        body = json.dumps(collect(), ensure_ascii=False)
        self._send(200, body, "application/json")

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    Server(("0.0.0.0", PORT), Handler).serve_forever()
