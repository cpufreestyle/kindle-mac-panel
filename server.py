#!/usr/bin/env python3
# Kindle 面板数据服务器 — psutil 采集 macOS 系统指标
# 端口 18866，仅局域网使用。依赖：pip3 install --user psutil
import json
import os
import re
import socket
import time
import threading
import urllib.request
import psutil

BASE = os.path.dirname(os.path.abspath(__file__))   # 仓库根目录（相对路径，多机复用）
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORT = 18866
NET_IFACE = "en0"
_net_prev = {"ts": 0.0, "sent": 0, "recv": 0}
_PNG_CACHE = {}  # sparkpng 渲染结果缓存 {name: (ts, png_bytes)}
HIST_CPU = []      # CPU 占用历史（60 点 ≈ 15 分钟）
HIST_MEM = []      # 内存占用历史
HIST_TEMP = []     # CPU 温度历史（None 表示该次无读数）
HIST_SWAP = []     # Swap 占用历史（percent）
HIST_DISK = []     # 数据卷占用历史（percent）
HIST_GPU = []      # GPU 占用历史
HIST_KB = []       # Kindle 电池历史（percent）
HIST_KM = []       # Kindle 内存历史
HIST_KD = []       # Kindle 存储历史
HIST_TW = []       # Airport 温度历史
HIST_TN = []       # NAND 温度历史
HIST_TB = []       # 电池温度历史
HIST_IOR = []      # 磁盘读 MB/s
HIST_IOW = []      # 磁盘写 MB/s
HIST_ND = []       # 网络下行 KB/s
HIST_NU = []       # 网络上行 KB/s
HIST_MCOMP = []    # 压缩内存 GB
HIST_MSWAP = []    # Swap GB
HIST_DGXU = []     # DGX GPU 利用率（%）
HIST_DGXT = []     # DGX GPU 温度（°C）

# ── Windows 主机接入（NUC9 等）──────────────────────────────────────────────
# Windows 侧采集器（win-metrics.ps1）每 5s POST 一次 JSON 到 /api/push/win，
# Mac 端缓存最新快照，面板渲染 WIN 卡。数据超过 WIN_TTL 视为离线。
WIN_TTL = 30.0
_WIN = {"ts": 0.0, "data": None}
_WIN_LOCK = threading.Lock()
HIST_WCPU = []     # Windows CPU 使用率（%）
HIST_WMEM = []     # Windows 内存使用率（%）
HIST_WDISK = []    # Windows 磁盘使用率（%）
HIST_WNETD = []    # Windows 下行 KB/s
HIST_WNETU = []    # Windows 上行 KB/s

# 允许局域网下载的 Windows 侧脚本（白名单）
WIN_FILES = ("win-metrics.ps1", "install-win-metrics.ps1")

# 历史持久化文件（与 BASE 同目录 —— 2026-11-22 项目迁到 AI Shared/repo/ 后，
# 原先写死的绝对路径会指到旧位置；用 BASE 推导才能跟着目录走）
HIST_FILE = os.path.join(BASE, "hists.json")
QSTATS_FILE = os.environ.get("QSTATS_METRICS", "/tmp/qstats-metrics.json")
QSTATS_TTL = 15.0

SAMPLE_INTERVAL = 5.0
_SNAP_LOCK = threading.Lock()
_SAMPLE_LOCK = threading.Lock()
_SNAP = {"data": None}
_SAMPLER_STARTED = False


def _hload():
    """服务器启动时恢复历史（内存态数组重启即丢的根治）"""
    try:
        d = json.load(open(HIST_FILE))
    except Exception:
        return
    HIST_CPU[:] = d.get("cpu") or []
    HIST_MEM[:] = d.get("mem") or []
    HIST_TEMP[:] = d.get("temp") or []
    HIST_SWAP[:] = d.get("swap") or []
    HIST_DISK[:] = d.get("disk") or []
    HIST_GPU[:] = d.get("gpu") or []
    HIST_KB[:] = d.get("kb") or []
    HIST_KM[:] = d.get("km") or []
    HIST_KD[:] = d.get("kd") or []
    HIST_TW[:] = d.get("tw") or []
    HIST_TN[:] = d.get("tn") or []
    HIST_TB[:] = d.get("tb") or []
    HIST_IOR[:] = d.get("ior") or []
    HIST_IOW[:] = d.get("iow") or []
    HIST_ND[:] = d.get("nd") or []
    HIST_NU[:] = d.get("nu") or []
    HIST_MCOMP[:] = d.get("mcomp") or []
    HIST_MSWAP[:] = d.get("mswap") or []
    HIST_DGXU[:] = d.get("dgxu") or []
    HIST_DGXT[:] = d.get("dgxt") or []
    HIST_WCPU[:] = d.get("wcpu") or []
    HIST_WMEM[:] = d.get("wmem") or []
    HIST_WDISK[:] = d.get("wdisk") or []
    HIST_WNETD[:] = d.get("wnetd") or []
    HIST_WNETU[:] = d.get("wnetu") or []


_HIST_LOCK = threading.Lock()


def _hsave():
    # 采样线程和 HTTP 推送线程都会调；不加锁时 json.dump 遍历 list 会撞上
    # 另一线程的 append，可能抛 "list changed size during iteration" 并写坏文件。
    try:
        with _HIST_LOCK:
            payload = {"cpu": HIST_CPU, "mem": HIST_MEM, "temp": HIST_TEMP,
                       "swap": HIST_SWAP, "disk": HIST_DISK, "gpu": HIST_GPU,
                       "kb": HIST_KB, "km": HIST_KM, "kd": HIST_KD,
                       "tw": HIST_TW, "tn": HIST_TN, "tb": HIST_TB,
                       "ior": HIST_IOR, "iow": HIST_IOW, "nd": HIST_ND, "nu": HIST_NU,
                       "mcomp": HIST_MCOMP, "mswap": HIST_MSWAP,
                       "dgxu": HIST_DGXU, "dgxt": HIST_DGXT,
                       "wcpu": HIST_WCPU, "wmem": HIST_WMEM, "wdisk": HIST_WDISK,
                       "wnetd": HIST_WNETD, "wnetu": HIST_WNETU}
            # 先拷一份快照再序列化，避免持锁期间 list 被改
            snap = {k: list(v) for k, v in payload.items()}
            # 同目录临时文件 + 原子替换：写一半崩溃也不会留下半个 hists.json
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(HIST_FILE),
                                       prefix=".hists-", suffix=".tmp")
            try:
                # mkstemp 默认 600，替换前把原文件权限还原回去
                try:
                    os.chmod(tmp, os.stat(HIST_FILE).st_mode & 0o777)
                except OSError:
                    os.chmod(tmp, 0o644)
                with os.fdopen(fd, "w") as f:
                    json.dump(snap, f)
                os.replace(tmp, HIST_FILE)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except Exception:
        pass


_hload()


def _qstats_read():
    """qstats 每 5s 导出的本地快照；过期或不存在时返回 None。"""
    try:
        with open(QSTATS_FILE) as f:
            doc = json.load(f)
        if time.time() - float(doc.get("ts", 0)) <= QSTATS_TTL:
            return doc
    except Exception:
        pass
    return None


def _qstats_sensor(doc, key):
    for sensor in (doc or {}).get("sensors") or []:
        if sensor.get("key") == key:
            value = sensor.get("value")
            if isinstance(value, (int, float)):
                return float(value)
    return None


MAGPIE_METRICS_FILE = os.environ.get("MAGPIE_METRICS_FILE", "/tmp/magpie-bridge-metrics.json")


def _magpie_read():
    """magpie-bridge 每 10s 导出的模型健康快照；过期或不存在时返回 None。"""
    try:
        with open(MAGPIE_METRICS_FILE) as f:
            doc = json.load(f)
        if doc.get("app") == "magpie-bridge" and time.time() - float(doc.get("ts", 0)) <= QSTATS_TTL:
            return doc
    except Exception:
        pass
    return None


def mac_lan_ip():
    """返回 en0 的局域网 IPv4，供 Kindle 在 USB 连接时自发现 Mac。"""
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if name != "en0":
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address and not addr.address.startswith("127."):
                    return addr.address
    except Exception:
        pass
    return ""

_BARS = "▁▂▃▄▅▆▇█"


def _spark(vals, vmax, width=26):
    vs = [v for v in vals if v is not None][-width:]
    if len(vs) < 2:
        return ""
    step = (vmax or 1) / 7.0
    return "".join(_BARS[min(7, int(v / step))] for v in vs)


def spark_payload():
    """四条趋势 span 的最新波形（供 /spark.json 与面板首渲染共用；峰值在 .dt 静态行）
    层级：CPU/内存主指标 44px×21 点，磁盘次级 34px×26 点，Swap 行内小字 20 点
    wcpu/wmem/wdisk 是 Windows 主机的标量值，script.js 按 id 更新 WIN 卡文本"""
    _w = win_snapshot()
    return {"spc": _spark(HIST_CPU, 100, 21), "spm": _spark(HIST_MEM, 100, 21),
            "sps": _spark(HIST_SWAP, 100, 20), "spd": _spark(HIST_DISK, 100, 26),
            "wcpu": ("%.0f" % _w["cpu"]) if _w.get("ok") and isinstance(_w.get("cpu"), (int, float)) else "",
            "wmem": ("%.0f" % _w["memPct"]) if _w.get("ok") and isinstance(_w.get("memPct"), (int, float)) else "",
            "wdisk": ("%.0f" % _w["diskPct"]) if _w.get("ok") and isinstance(_w.get("diskPct"), (int, float)) else ""}


def _hmax2(vals):
    filled = [v for v in vals if v is not None]
    return int(max(filled or [0]))


def _hlast(vals):
    filled = [v for v in vals if v is not None]
    return filled[-1] if filled else 0


def _dgx_val(vals, fmt):
    """DGX 图例值：无样本时显示 --（与 0% 区分，离线一眼可辨）"""
    filled = [v for v in vals if v is not None]
    return (fmt % filled[-1]) if filled else "--"


def _smooth_path(pts, seg=8):
    """Catmull-Rom 样条插值：控制点 → 密集平滑点列"""
    smooth = []
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else p2
        for t in range(seg):
            t /= float(seg)
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            smooth.append((x, y))
    smooth.append(pts[-1])
    return smooth


_FONTS = {}
def _get_font(size):
    if size not in _FONTS:
        from PIL import ImageFont
        try:
            _FONTS[size] = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", size)
        except Exception:
            _FONTS[size] = None
    return _FONTS[size]

def render_curves_png(series, w=900, h=110, legend_items=None, font_size=34, y_max=None, compact=False):
    """多序列平滑曲线 PNG。series: [(vals, gray, width), ...]
    legend_items: [(text, gray, width), ...] 每项前画对应线型样本，颜色一一对应
    2x 超采样抗锯齿；e-ink 友好"""
    import io
    from PIL import Image, ImageDraw, ImageFont
    S = 2
    W, H = w * S, h * S
    img = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(img)
    fnt = _get_font(font_size * S)
    legend_h = 34 if compact else 52
    if legend_items:
        lx = 8 * S
        lyc = (16 if compact else 20) * S  # 图例行的垂直中心
        block = (14 if compact else 18) * S
        for text, gray, lw in legend_items:
            top = lyc - block // 2
            dr.rectangle(
                [lx, top, lx + block, top + block],
                fill=gray,
                outline=0 if gray >= 150 else None,
                width=1 * S
            )
            lx += block + 5 * S
            line_w = (18 if compact else 22) * S
            dr.line([(lx, lyc), (lx + line_w, lyc)], fill=gray, width=max(2, lw) * S)
            lx += line_w + 5 * S
            dr.text((lx, 2 * S), text, fill=0, font=fnt)
            try:
                lx += int(dr.textlength(text, font=fnt)) + 12 * S
            except Exception:
                lx += len(text) * font_size * S + 12 * S
    for vals, gray, lw in series:
        vs = [v for v in vals if v is not None][-240:]
        if len(vs) < 2:
            continue
        mx = y_max if y_max else max(max(vs), 1.0)
        n = len(vs)
        pts = []
        for i, v in enumerate(vs):
            x = int(i * (W - 40) / (n - 1)) + 20
            y = H - 8 * S - int(min(v, mx) / mx * (H - legend_h * S))
            pts.append((x, y))
        dr.line(_smooth_path(pts), fill=gray, width=lw * S)
        lx2, ly2 = pts[-1]
        r = 4 * S
        dr.ellipse([lx2 - r, ly2 - r, lx2 + r, ly2 + r], fill=gray)
    img = img.resize((w, h), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()


def render_curve_png(vals, label="", w=900, h=110):
    """单序列兼容包装"""
    filled = [v for v in vals if v is not None]
    cur = int(filled[-1]) if filled else 0
    items = ([(label + " %d%%" % cur, 0, 3)] if label else [])
    return render_curves_png([(vals, 0, 3)], w, h, legend_items=items)


def render_memcomp_png(mem, w=900, h=100):
    """内存构成条：App 黑 / Wired 深灰 / Compressed 浅灰 / Free 白。"""
    import io
    from PIL import Image, ImageDraw, ImageFont
    S = 4
    W, H = w * S, h * S
    img = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(img)
    total = max(float(mem.get("totalGB") or 0), 1.0)
    app = max(float(mem.get("appGB") or 0), 0.0)
    wired = max(float(mem.get("wiredGB") or 0), 0.0)
    comp = max(float(mem.get("compGB") or 0), 0.0)
    free = max(total - app - wired - comp, 0.0)
    x0, x1 = 20 * S, W - 20 * S
    y0, bh = 8 * S, 40 * S
    dr.rectangle([x0, y0, x1, y0 + bh], outline=0, width=3 * S, fill=255)
    x = x0
    for value, gray in ((app, 0), (wired, 80), (comp, 160)):
        seg = int((x1 - x0) * value / total)
        if seg > 0:
            dr.rectangle([x, y0 + 2 * S, min(x + seg, x1), y0 + bh - 2 * S], fill=gray)
            x += seg
    try:
        fnt = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 32 * S)
    except Exception:
        fnt = None
    label = "App %.1fG · Wired %.1fG · Comp %.1fG · Free %.1fG" % (app, wired, comp, free)
    dr.text((20 * S, 58 * S), label, fill=0, font=fnt)
    img = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_membar_png(mem, w=900, h=34):
    """细内存构成条，无文字；用于在趋势折线图上方做当前构成快照。"""
    import io
    from PIL import Image, ImageDraw
    S = 4
    W, H = w * S, h * S
    img = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(img)
    total = max(float(mem.get("totalGB") or 0), 1.0)
    app = max(float(mem.get("appGB") or 0), 0.0)
    wired = max(float(mem.get("wiredGB") or 0), 0.0)
    comp = max(float(mem.get("compGB") or 0), 0.0)
    x0, x1 = 10 * S, W - 10 * S
    y0, bh = 3 * S, H - 6 * S
    dr.rectangle([x0, y0, x1, y0 + bh], outline=0, width=3 * S, fill=255)
    x = x0
    for value, gray in ((app, 0), (wired, 80), (comp, 160)):
        seg = int((x1 - x0) * value / total)
        if seg > 0:
            dr.rectangle([x, y0 + 2 * S, min(x + seg, x1), y0 + bh - 2 * S], fill=gray)
            x += seg
    img = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_memcombo_png(mem, w=900, h=142):
    """内存卡单图：上方构成条 + 下方 Comp/Swap 折线。"""
    import io
    from PIL import Image, ImageDraw, ImageFont
    canvas = Image.new("L", (w, h), 255)
    dr = ImageDraw.Draw(canvas)
    total = max(float(mem.get("totalGB") or 0), 1.0)
    app = max(float(mem.get("appGB") or 0), 0.0)
    wired = max(float(mem.get("wiredGB") or 0), 0.0)
    comp = max(float(mem.get("compGB") or 0), 0.0)
    x0, x1, y0, bh = 10, w - 10, 4, 26
    dr.rectangle([x0, y0, x1, y0 + bh], outline=0, width=2, fill=255)
    x = x0
    for value, gray in ((app, 0), (wired, 80), (comp, 160)):
        seg = int((x1 - x0) * value / total)
        if seg > 0:
            dr.rectangle([x, y0 + 2, min(x + seg, x1), y0 + bh - 2], fill=gray)
            x += seg
    chart = render_curves_png(
        [(HIST_MCOMP, 0, 4), (HIST_MSWAP, 110, 3)],
        w=w, h=81, font_size=28, y_max=total,
        legend_items=[("Comp %.1fG" % _hlast(HIST_MCOMP), 0, 4),
                      ("Swap %.1fG" % _hlast(HIST_MSWAP), 110, 3)])
    canvas.paste(Image.open(io.BytesIO(chart)).convert("L"), (0, 34))
    try:
        fnt = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 22)
    except Exception:
        fnt = None
    x, y, block = 10, 120, 14
    for label, gray in (("App", 0), ("Wired", 80), ("Comp", 160), ("Free", 255)):
        dr.rectangle([x, y, x + block, y + block], fill=gray, outline=0, width=1)
        x += block + 5
        dr.text((x, y - 3), label, fill=0, font=fnt)
        try:
            x += int(dr.textlength(label, font=fnt)) + 12
        except Exception:
            x += len(label) * 12 + 12
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()


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


def _vmstat_mem():
    """vm_stat → 与 qstats 同口径的内存四段分解。

    口径（与 qstats 的 ram 字段保持一致，切换数据源时指标定义不变）：
        free = free_pages + speculative + file_backed(cache)
        used = total - free
        wired = Pages wired down
        comp  = Pages occupied by compressor
        app   = used - wired - comp
    任一项解析失败返回 None，调用方回退 psutil。
    """
    try:
        import subprocess as _sp
        out = _sp.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"page size of (\d+)", out)
        if not m:
            return None
        ps = int(m.group(1))

        def pg(key):
            mm = re.search(re.escape(key) + r":\s+(\d+)\.", out)
            if not mm:
                return None
            return int(mm.group(1)) * ps

        wired = pg("Pages wired down")
        comp = pg("Pages occupied by compressor")
        free_p = pg("Pages free")
        spec = pg("Pages speculative")
        cache = pg("File-backed pages")
        if None in (wired, comp, free_p, spec, cache):
            return None
        return {"wired": wired, "comp": comp, "free": free_p + spec + cache}
    except Exception:
        return None


def _collect_uncached():
    qs = _qstats_read()
    usage = round(psutil.cpu_percent(interval=0.3), 1)
    if qs:
        _v = (qs.get("cpu") or {}).get("usage")
        if isinstance(_v, (int, float)):
            usage = round(_v * 100, 1)
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
    if qs:
        _v = (qs.get("gpu") or {}).get("usage")
        if isinstance(_v, (int, float)):
            gpu_live = round(_v * 100)
    HIST_CPU.append(usage)
    if len(HIST_CPU) > 240:
        HIST_CPU.pop(0)
    HIST_GPU.append(gpu_live)
    if len(HIST_GPU) > 240:
        HIST_GPU.pop(0)

    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    du = psutil.disk_usage("/System/Volumes/Data")   # 真实数据卷（/ 是系统快照假象）
    mem_used, mem_total, mem_pct = vm.used, vm.total, vm.percent
    disk_used, disk_free, disk_total, disk_pct = du.used, du.free, du.total, du.percent
    mem_app, mem_wired, mem_comp = vm.used, 0.0, 0.0
    mem_free = max(vm.total - vm.used, 0)
    mem_swap, mem_pressure = sm.used, "--"
    if not qs:
        # 本机模式：psutil 不提供 compressed，且 used 的分母口径与 qstats 不同。
        # 改用 vm_stat 推导四段，保证 App/Wired/Comp/Free 自洽且与 qstats 口径一致。
        _vs = _vmstat_mem()
        if _vs:
            mem_total = vm.total
            mem_free = _vs["free"]
            mem_used = mem_total - mem_free
            mem_pct = (mem_used / mem_total * 100) if mem_total else vm.percent
            mem_wired = _vs["wired"]
            mem_comp = _vs["comp"]
            mem_app = max(mem_used - mem_wired - mem_comp, 0)
    if qs:
        _r = qs.get("ram") or {}
        if isinstance(_r.get("used"), (int, float)):
            mem_used = _r["used"]
        if isinstance(_r.get("total"), (int, float)):
            mem_total = _r["total"]
        if isinstance(_r.get("usage"), (int, float)):
            mem_pct = _r["usage"] * 100
        if isinstance(_r.get("app"), (int, float)):
            mem_app = _r["app"]
        if isinstance(_r.get("wired"), (int, float)):
            mem_wired = _r["wired"]
        if isinstance(_r.get("compressed"), (int, float)):
            mem_comp = _r["compressed"]
        if isinstance(_r.get("free"), (int, float)):
            mem_free = _r["free"]
        elif isinstance(_r.get("used"), (int, float)) and isinstance(_r.get("total"), (int, float)):
            mem_free = max(_r["total"] - _r["used"], 0)
        if isinstance(_r.get("swap"), (int, float)):
            mem_swap = _r["swap"]
        if isinstance(_r.get("pressure"), str):
            mem_pressure = _r["pressure"]
        _d = qs.get("disk") or {}
        if isinstance(_d.get("used"), (int, float)):
            disk_used = _d["used"]
        if isinstance(_d.get("free"), (int, float)):
            disk_free = _d["free"]
        if isinstance(_d.get("total"), (int, float)):
            disk_total = _d["total"]
        if isinstance(_d.get("usage"), (int, float)):
            disk_pct = _d["usage"] * 100

    batt = psutil.sensors_battery()
    batt_out = None
    if batt:
        left = batt.secsleft if isinstance(batt.secsleft, int) and batt.secsleft > 0 else None
        batt_out = {"percent": batt.percent, "charging": bool(batt.power_plugged), "left": left}
    if qs:
        _b = qs.get("battery") or {}
        if isinstance(_b.get("level"), (int, float)):
            batt_out = {
                "percent": round(_b["level"] * 100, 1),
                "charging": bool(_b.get("charging")),
                "left": (batt_out or {}).get("left"),
            }

    io = psutil.disk_io_counters()
    io_out = None
    if io:
        now = time.time()
        if _IO_PREV["ts"] and now - _IO_PREV["ts"] > 5:
            dt = now - _IO_PREV["ts"]
            io_out = {"rMBs": round(max(0, io.read_bytes - _IO_PREV["r"]) / dt / 1048576, 1),
                      "wMBs": round(max(0, io.write_bytes - _IO_PREV["w"]) / dt / 1048576, 1)}
            _IO_PREV.update(ts=now, r=io.read_bytes, w=io.write_bytes)
        elif not _IO_PREV["ts"]:
            _IO_PREV.update(ts=now, r=io.read_bytes, w=io.write_bytes)
    if qs:
        _io = qs.get("disk_io") or {}
        _r, _w = _io.get("read"), _io.get("write")
        if isinstance(_r, (int, float)) and isinstance(_w, (int, float)):
            io_out = {"rMBs": round(_r / 1048576, 1), "wMBs": round(_w / 1048576, 1)}

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
        elif not _net_prev["ts"]:
            _net_prev.update(ts=now, recv=nio.bytes_recv, sent=nio.bytes_sent)
        net_out = r
    if qs:
        _n = qs.get("net") or {}
        _down, _up = _n.get("download"), _n.get("upload")
        if isinstance(_down, (int, float)) and isinstance(_up, (int, float)):
            net_out = dict(net_out or {"totalMB": round(_NET_MB[0], 1)})
            net_out["downKBs"] = round(_down / 1024.0, 1)
            net_out["upKBs"] = round(_up / 1024.0, 1)

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
    if qs:
        for key, sensor_key in (("pstr", "PSTR"), ("avg_pstr", "Average System Total"),
                                ("tb1t", "TB1T"), ("th0x", "TH0x"), ("tw0p", "TW0P"),
                                ("id0r", "ID0R"), ("vd0r", "VD0R")):
            _v = _qstats_sensor(qs, sensor_key)
            if _v is not None:
                sensors[key] = _v
        _v = _qstats_sensor(qs, "F0Ac")
        if _v is not None:
            sensors["fan"] = _v
            sensors["f0ac"] = _v
        _v = _qstats_sensor(qs, "Hottest CPU") or _qstats_sensor(qs, "Average CPU")
        if _v is not None:
            sensors["cpu_temp"] = _v
        _v = _qstats_sensor(qs, "Average GPU")
        if _v is not None:
            sensors["gpu"] = _v

    HIST_MEM.append(round(mem_pct, 1))
    if len(HIST_MEM) > 240:
        HIST_MEM.pop(0)
    HIST_SWAP.append(round(sm.percent, 1))
    if len(HIST_SWAP) > 240:
        HIST_SWAP.pop(0)
    HIST_DISK.append(round(disk_pct, 1))
    if len(HIST_DISK) > 240:
        HIST_DISK.pop(0)
    _t = sensors.get("cpu_temp")
    HIST_TEMP.append(_t if isinstance(_t, (int, float)) else None)
    if len(HIST_TEMP) > 240:
        HIST_TEMP.pop(0)
    for _harr, _skey in ((HIST_TW, "tw0p"), (HIST_TN, "th0x"), (HIST_TB, "tb1t")):
        _sv = sensors.get(_skey)
        _harr.append(_sv if isinstance(_sv, (int, float)) else None)
        if len(_harr) > 240:
            _harr.pop(0)
    HIST_IOR.append(io_out.get("rMBs") if io_out else None)
    HIST_IOW.append(io_out.get("wMBs") if io_out else None)
    HIST_ND.append(net_out.get("downKBs") if net_out else None)
    HIST_NU.append(net_out.get("upKBs") if net_out else None)
    HIST_MCOMP.append(round(mem_comp / 1024 ** 3, 2) if mem_comp else 0.0)
    HIST_MSWAP.append(round(mem_swap / 1024 ** 3, 2) if mem_swap else 0.0)
    # DGX（spark-monitor 主机）GPU 利用率/温度：与 Mac 指标同频采样，面板和 Kindle 共用同一张折线
    _dgx = spark_snapshot()
    HIST_DGXU.append(_dgx.get("util") if isinstance(_dgx.get("util"), (int, float)) else None)
    HIST_DGXT.append(_dgx.get("temp") if isinstance(_dgx.get("temp"), (int, float)) else None)
    for _harr in (HIST_IOR, HIST_IOW, HIST_ND, HIST_NU, HIST_MCOMP, HIST_MSWAP,
                  HIST_DGXU, HIST_DGXT):
        if len(_harr) > 240:
            _harr.pop(0)
    _hsave()

    swap_used = sm.used / 1024 ** 3
    avg15 = round(sum(HIST_CPU) / len(HIST_CPU), 1) if len(HIST_CPU) >= 3 else usage

    return {
        "ts": time.strftime("%H:%M:%S"),
        "cpu": {"usage": usage, "avg15": avg15, "temp": sensors.get("cpu_temp")},
        "mem": {
            "usedGB": round(mem_used / 1024 ** 3, 1),
            "totalGB": round(mem_total / 1024 ** 3, 1),
            "usage": round(mem_pct, 1),
            "appGB": round(mem_app / 1024 ** 3, 1),
            "wiredGB": round(mem_wired / 1024 ** 3, 1),
            "compGB": round(mem_comp / 1024 ** 3, 1),
            "freeGB": round(mem_free / 1024 ** 3, 1),
            "swapGB": round(mem_swap / 1024 ** 3, 1),
            "pressure": mem_pressure,
        },
            "swap": {"usedGB": round(sm.used / 1024 ** 3, 1)},
        "disk": {
            "usedGB": round(disk_used / 1024 ** 3, 1),
            "freeGB": round(disk_free / 1024 ** 3, 0),
            "totalGB": round(disk_total / 1024 ** 3, 1),
            "usage": round(disk_pct, 1),
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
        "source": "qstats" if qs else "local",
        "win": win_snapshot(),
    }


def _sample_and_store():
    with _SAMPLE_LOCK:
        data = _collect_uncached()
    with _SNAP_LOCK:
        _SNAP["data"] = data
    return data


def _start_sampler():
    global _SAMPLER_STARTED
    if _SAMPLER_STARTED:
        return
    _SAMPLER_STARTED = True

    def loop():
        while True:
            try:
                _sample_and_store()
            except Exception:
                import traceback
                try:
                    with open("/tmp/kindle-sampler.log", "a") as f:
                        f.write(time.strftime("%H:%M:%S ") + traceback.format_exc() + "\n")
                except Exception:
                    pass
            time.sleep(SAMPLE_INTERVAL)

    threading.Thread(target=loop, daemon=True).start()


def win_push(payload, peer="?"):
    """接收 Windows 采集器推送的 JSON 快照。字段缺失或类型不对一律丢弃。"""
    if not isinstance(payload, dict):
        return False

    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    out = {"host": str(payload.get("host") or "WIN")[:32]}
    for k in ("cpu", "memPct", "diskPct", "netDownKBs", "netUpKBs",
              "memUsedGB", "memTotalGB", "diskUsedGB", "diskTotalGB",
              "diskFreeGB", "uptimeSec", "tempC"):
        v = num(payload.get(k))
        if v is not None:
            out[k] = v
    if "cpu" not in out:
        _winlog("REJECT from=%s host=%r keys=%r（缺 cpu）" % (peer, out.get("host"), sorted(payload.keys())))
        return False
    out["ts"] = time.time()
    with _WIN_LOCK:
        _WIN["data"] = out
        _WIN["ts"] = out["ts"]
    # 追加历史曲线（每个 push 一点）
    for arr, key in ((HIST_WCPU, "cpu"), (HIST_WMEM, "memPct"),
                     (HIST_WDISK, "diskPct"), (HIST_WNETD, "netDownKBs"),
                     (HIST_WNETU, "netUpKBs")):
        arr.append(out.get(key))
        if len(arr) > 240:
            arr.pop(0)
    _hsave()
    _winlog("OK from=%s host=%r cpu=%s mem=%s%% disk=%s%% down=%s up=%s" % (
        peer, out.get("host"), out.get("cpu"), out.get("memPct"), out.get("diskPct"),
        out.get("netDownKBs"), out.get("netUpKBs")))
    return True


def _winlog(msg):
    """推送日志（排查「离线」用）：/tmp/kindle-win.log"""
    try:
        with open("/tmp/kindle-win.log", "a") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def win_snapshot():
    """Windows 主机最新快照；超过 WIN_TTL 视为离线（返回 ok=False，不阻断面板）。"""
    with _WIN_LOCK:
        data = _WIN["data"]
        ts = _WIN["ts"]
    age = round(time.time() - ts, 1) if ts else 0.0
    if not data or age > WIN_TTL:
        return {"ok": False, "host": (data or {}).get("host", "WIN"), "age": age}
    out = dict(data)
    out["ok"] = True
    out["age"] = age
    return out


def collect():
    _start_sampler()
    with _SNAP_LOCK:
        data = _SNAP["data"]
    if data is not None:        return data
    return _sample_and_store()


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



# ── DGX / spark-monitor 接入 ────────────────────────────────────────────────
# spark-monitor 在 127.0.0.1:8888 提供 /api/status（本地只读）。这里做 5s 缓存，
# 任何失败都降级成 ok=False，绝不让 Kindle 页面 500。
SPARK_URL = "http://127.0.0.1:8888/api/status"
_SPARK = {"ts": 0.0, "data": None}


def spark_snapshot(ttl=5):
    now = time.time()
    if _SPARK["data"] is not None and now - _SPARK["ts"] < ttl:
        return _SPARK["data"]
    out = {"ok": False, "host": "DGX", "util": None, "temp": None,
           "power": None, "mem_used": None, "mem_total": None, "ssh": "offline"}
    try:
        with urllib.request.urlopen(SPARK_URL, timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        hosts = d.get("hosts") or {}
        h = hosts.get("spark") or (list(hosts.values())[0] if hosts else {})
        gpu = h.get("gpu") or {}
        mem = h.get("mem") or {}
        out = {
            "ok": True,
            "host": h.get("hostname") or "DGX",
            "util": gpu.get("util"),
            "temp": gpu.get("temp"),
            "power": gpu.get("power"),
            "mem_used": mem.get("used"),
            "mem_total": mem.get("total"),
            "ssh": ((d.get("ssh") or {}).get("status") or "unknown"),
        }
    except Exception:
        pass
    _SPARK["data"] = out
    _SPARK["ts"] = now
    return out


PANEL_CSS = """
html { margin:0; padding:0; background:#fff; color:#000; width:100%; min-height:100%;
  overflow:auto; font-family:"Microsoft YaHei","Noto Sans CJK",sans-serif; }
body { margin:0; padding:0; background:#fff; color:#000; width:100%; min-height:100%; }
/* Kindle（fetchloop 用 curl 抓的那份）固定一屏不滚动：墨水屏 1072×1448，
   内容超了也够不着，只能靠服务端少给内容；浏览器版文档自然增长，可滚动 */
html.k { height:100%; overflow:hidden; }
html.k body { height:100%; overflow:hidden; }
#app { min-height:100%; box-sizing:border-box; padding:8px 22px 10px 22px; }
#head { overflow:hidden; border-bottom:4px solid #000; padding-bottom:4px; }
#title { float:left; font-size:44px; font-weight:bold; letter-spacing:0.08em; }
#ts { float:right; font-size:26px; color:#222; font-weight:bold; }
.card { box-sizing:border-box; border:3px solid #000; padding:4px 8px 4px 8px; margin-bottom:3px; }
.card.compact { padding:2px 8px 3px 8px; }
.sech { font-size:26px; font-weight:bold; margin-bottom:0; }
.sech.compact { font-size:22px; }
.row { overflow:hidden; margin-top:6px; }
.label { float:left; width:96px; font-size:24px; font-weight:bold; }
.bar { overflow:hidden; height:22px; background:#fff; border:3px solid #000;
  margin:0 12px; }
.fill { height:100%; background:#000; }
.val { float:right; width:130px; text-align:right; font-size:24px; font-weight:bold; }
.big { font-size:40px; font-weight:bold; line-height:1.1; margin-top:2px; }
.big .g { color:#666; }
.kv { font-size:26px; font-weight:bold; color:#111; margin-top:2px; }
.dt { font-size:22px; color:#333; margin-top:2px; font-weight:bold; }
img.chart { display:block; width:100%; height:auto; margin-top:1px; }
.spark { font-size:44px; line-height:1; margin-top:6px; overflow:hidden; white-space:nowrap; }
.spark.s2 { font-size:34px; margin-top:5px; }
.kb { margin-top:8px; }
.kbl { overflow:hidden; width:100%; font-size:24px; font-weight:bold; }
.kbl span:first-child { float:left; }
.kbl span:last-child { float:right; }
.kbt { margin-top:4px; height:22px; border:3px solid #000;
  background:#fff; overflow:hidden; }
.kbf { height:100%; background:#000; }
.kline { margin-top:4px; font-size:24px; color:#333; font-weight:bold; }
#advice { font-size:24px; color:#222; margin-top:4px; font-weight:bold; }
"""


_COND = {}   # 建议防抖：条件 -> 连续命中次数（fetchloop 15s 一采样，≥3 次≈45s 确认）

# 面板页面轮询脚本（外链 script.js）——象棋/番茄钟验证过的加载模式
SCRIPT_JS = (
    "var __tg=false;"
    "var _vp=new Image();_vp.src='/vp?w='+window.innerWidth+'&h='+window.innerHeight;"
    "var tk0=document.getElementById('tick');"
    "if(tk0)tk0.textContent='已加载';"          # 探针①：script.js 一执行立即置位
    "setInterval(function(){"
    "var tk=document.getElementById('tick');"
    "if(tk)tk.textContent='JS活';"              # 探针②：定时器在跑
    "var x=new XMLHttpRequest();"
    "x.open('GET','spark.json?v='+Date.now(),1);"
    "x.onreadystatechange=function(){"
    "if(x.readyState==4&&x.status==200){"
    "try{var d=JSON.parse(x.responseText);for(var k in d){"
    "var e=document.getElementById(k);if(e)e.textContent=d[k];}}catch(e){}"
    "var tg=(__tg=!__tg);"
    "for(var c in window.__tgnm){var im=document.getElementById(c);"
    "if(im)im.src='spark'+window.__tgnm[c]+'_'+(tg?'a':'b')+'.png';}"
    "}};x.send()},8000);"
    "window.__tgnm={cv:'cpu',cv2:'membar',cv3:'diskread',cv4:'diskwrite',cv5:'netdown',cv6:'netup',cv7:'memtrend',cv8:'dgxutil',cv9:'dgxtemp',cv10:'wincpu',cv11:'winnet'};"
)

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
        tips.append("Swap 高(%sG) 建议重启 Mac" % swp)
    elif _bump("swap_mid", swp >= 2):
        tips.append("Swap 偏高(%sG)" % swp)
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
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>运维值班台 24h 历史</title>'
            '<style>body{font-family:"Microsoft YaHei",sans-serif;margin:30px;color:#000}'
            'table{border-collapse:collapse;margin-top:16px}td{border:1px solid #999;'
            'padding:6px 18px}h2{margin-bottom:4px}.lg span{margin-right:24px}</style></head><body>'
            '<h2>运维值班台 24 小时历史</h2>'
            '<div class="lg"><span>━ CPU%（黑）</span><span>━ 温度°C（深灰）</span>'
            '<span>━ Swap G（浅灰，满量程 32G）</span></div>' + svg +
            '<table>' + rows + '</table><p>每 5 分钟采样 · refresh-now 可手动刷新数据</p></body></html>')


def _win_card_html(rev, kindle=False):
    """Windows 主机卡：从未收到过数据就不渲染（不占版面）；收到过但超时的显示离线。

    kindle=True（fetchloop 用 curl 抓的那份）只出文字行 —— 墨水屏 1072×1448
    装不下两张曲线图（body overflow:hidden 不滚动，会把底部内容裁掉）。
    """
    w = win_snapshot()
    host = w.get("host") or "WIN"
    if not w.get("ok"):
        # 离线：只在曾经收过数据时占一行提示
        if not _WIN["data"]:
            return ""
        return ('<div class="card compact win"><div class="sech compact">WIN · ' + host +
                ' · 离线（' + str(int(w.get("age") or 0)) + 's 前）</div></div>')

    def fnum(x, fmt):
        return (fmt % x) if isinstance(x, (int, float)) else "--"

    cpu = w.get("cpu")
    mem_pct = w.get("memPct")
    disk_pct = w.get("diskPct")
    mu, mt = w.get("memUsedGB"), w.get("memTotalGB")
    du, dt = w.get("diskUsedGB"), w.get("diskTotalGB")
    df = w.get("diskFreeGB")
    mem_s = ("%.0f/%.0fG" % (mu, mt)) if isinstance(mu, (int, float)) and isinstance(mt, (int, float)) else "--"
    disk_s = ("%.0f/%.0fG" % (du, dt)) if isinstance(du, (int, float)) and isinstance(dt, (int, float)) else "--"
    up = w.get("uptimeSec")
    up_s = ("%dd %dh" % (int(up) // 86400, (int(up) % 86400) // 3600)) if isinstance(up, (int, float)) else "--"
    temp_s = fnum(w.get("tempC"), "%.0f°C")
    down = w.get("netDownKBs")
    upk = w.get("netUpKBs")
    net_s = ("↓" + fnum(down, "%.0f") + " ↑" + fnum(upk, "%.0f") + " KB/s")
    head = ('<div class="sech compact">WIN · ' + host + ' · 在线 · CPU '
            + '<span id="wcpu">' + fnum(cpu, "%.0f") + '</span>% · 内存 '
            + '<span id="wmem">' + fnum(mem_pct, "%.0f") + '</span>% · 磁盘 '
            + '<span id="wdisk">' + fnum(disk_pct, "%.0f") + '</span>%</div>')
    detail = ('<div class="kv">内存 ' + mem_s + ' · 磁盘 ' + disk_s + ' · 剩 '
              + fnum(df, "%.0fG") + ' · 温度 ' + temp_s + ' · 运行 ' + up_s + '</div>')
    if kindle:
        # 墨水屏：文字两行，不放曲线图
        return ('<div class="card compact win">' + head +
                '<div class="kv">↓' + fnum(down, "%.0f") + ' ↑' + fnum(upk, "%.0f") +
                ' KB/s · ' + mem_s + ' · ' + disk_s + ' · ' + temp_s + '</div></div>')
    return (
        '<div class="card compact win">' + head
        + '<img id="cv10" class="chart" src="sparkwincpu' + rev + '.png">'
        + '<img id="cv11" class="chart" src="sparkwinnet' + rev + '.png">'
        + detail
        + '</div>'
    )


def render_panel(kindle=False):
    d = collect()
    _wx_read()

    # DGX / Codex status stay off the main panel so the Kindle layout fits one screen.

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
    pressure_s = {"normal": "正常", "warning": "偏高", "critical": "高"}.get(
        str(mem.get("pressure", "")).lower(), str(mem.get("pressure") or "--"))
    free_gb = (mem.get("totalGB") or 0) - (mem.get("usedGB") or 0)
    used_gb = mem.get("usedGB") or 0
    disk_used = disk.get("usedGB") or 0
    fan_v = d.get("fan")
    tw0p_v = d.get("tw0p")
    th0x_v = d.get("th0x")
    tb1t_v = d.get("tb1t")
    pstr_v = d.get("pstr")
    aps_v = d.get("avg_pstr")
    def fnum(x, fmt):
        return (fmt % x) if isinstance(x, (int, float)) else "--"

    def _hmax(vals):
        return int(max([v for v in vals if v is not None] or [0]))
    sp = spark_payload()
    _sp = spark_snapshot()
    _sp_on = bool(_sp.get("ok")) and _sp.get("ssh") == "connected"
    _sp_state = "在线" if _sp_on else ("未连" if _sp.get("ok") else "离线")
    _sp_host = _sp.get("host") or "DGX"
    _sp_big = ("%.0f%%" % _sp["util"]) if isinstance(_sp.get("util"), (int, float)) else "--"
    _sp_tmp = ("%.0f°C" % _sp["temp"]) if isinstance(_sp.get("temp"), (int, float)) else "--"
    _sp_pwr = ("%.1fW" % _sp["power"]) if isinstance(_sp.get("power"), (int, float)) else "--"
    _mu, _mt = _sp.get("mem_used"), _sp.get("mem_total")
    _sp_mem = ("%.0f/%.0fG" % (_mu / 1024.0, _mt / 1024.0)) if isinstance(_mu, (int, float)) and isinstance(_mt, (int, float)) else "--"
    qstats_linked = d.get("source") == "qstats"
    src_badge = " · Q · P3" if qstats_linked else " · P3"
    panel_title = "QSTATS" if qstats_linked else "运维值班台"
    _rev = "_a" if int(time.time()) % 2 == 0 else "_b"
    _temp_img = "sparkmemtrend" + _rev + ".png"
    return (
        '<!DOCTYPE html><html' + (' class="k"' if kindle else '') + '><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">'
        '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">'
        '<meta http-equiv="Pragma" content="no-cache">'
        '<meta http-equiv="Expires" content="0">'
        # meta refresh 300 已删：mesquite 每 300s 执行重载回调必崩（无触碰也弹「应用程序出错」的元凶）
        "<title>运维值班台</title><style>" + PANEL_CSS + "</style>"
        # 外链 script.js（象棋/番茄钟验证过的加载模式，内联 script 执行不稳定）：
        # 每 60s 轮询 spark.json 更新文本 + img 双缓冲切换；tick 探针显示 JS 心跳分钟
        '<script src="probe.js"></script></head>'
        '<body><div id="app">'
        '<div id="head"><span id="title">' + panel_title + '</span>'
        '<span id="ts">' + d["ts"] + src_badge + ((" · " + _WX["txt"]) if _WX["txt"] else "") + "</span></div>"
        # Stats 式分节（全部块级 + 已验证单层 float 行）
        '<div class="card"><div class="sech">CPU / GPU</div>'
        '<img id="cv" src="sparkcpu' + _rev + '.png" style="width:100%;height:auto">'
        '<div class="dt">峰 ' + str(_hmax(HIST_CPU)) + '% · 15分均 ' + str(avg15) + '% · CPU ' + temp_s + '</div></div>'
        '<div class="card"><div class="sech">RAM · ' + fnum(mem.get("usage"), "%.0f%%") + ' · 压力 ' + pressure_s + '</div>'
        '<img id="cv2" class="chart" src="sparkmembar' + _rev + '.png">'
        '<div class="dt">App ' + fnum(mem.get("appGB"), "%.1f") + ' · Wired ' + fnum(mem.get("wiredGB"), "%.1f") + ' · Comp ' + fnum(mem.get("compGB"), "%.1f") + ' · Free ' + fnum(mem.get("freeGB"), "%.1f") + 'G</div></div>'
        '<div class="card compact"><div class="sech compact">DISK · ' + fnum(disk.get("usage"), "%.0f%%") + ' · 剩 ' + disk_free_s + '</div>'
        '<img id="cv3" class="chart" src="sparkdiskread' + _rev + '.png">'
        '<img id="cv4" class="chart" src="sparkdiskwrite' + _rev + '.png">'
        '</div>'
        '<div class="card compact"><div class="sech compact">NET · ' + mac_ip + '</div>'
        '<img id="cv5" class="chart" src="sparknetdown' + _rev + '.png">'
        '<img id="cv6" class="chart" src="sparknetup' + _rev + '.png">'
        '</div>'
        '<div class="card"><div class="sech">TEMP · CPU ' + temp_s + '</div>'
        '<img id="cv7" class="chart" src="' + _temp_img + '">'
        '<div class="kv">NAND ' + fnum(th0x_v, "%.1f°C") + ' · 电池 ' + fnum(tb1t_v, "%.1f°C") + ' · 风扇 ' + fnum(fan_v, "%.0f") + ' rpm</div>'
        '<div class="kv">Kindle ' + kb_s + ' · ' + km_s + ' · ' + kd_s + ' · 功耗 ' + fnum(pstr_v, "%.1fW") + '（均 ' + fnum(aps_v, "%.1fW") + '）</div>'
        '</div>'
        + ('<div class="card compact"><div class="sech compact">DGX · ' + _sp_host + ' · ' + _sp_state + ' · GPU ' + _sp_big + ' · ' + _sp_tmp + ' · ' + _sp_pwr + '</div>'
           '<img id="cv8" class="chart" src="sparkdgxutil' + _rev + '.png">'
           '<img id="cv9" class="chart" src="sparkdgxtemp' + _rev + '.png">'
           '</div>') if _sp_on else ''
        + _win_card_html(_rev, kindle)
        + '<div id="advice">' + advice + ' · 已运行 ' + d["uptime"] + '</div>'
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
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_bytes(self, code, data, ctype):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
        # Windows 主机指标推送（win-metrics.ps1 → POST JSON）
        if self.path.startswith("/api/push/win"):
            peer = self.client_address[0] if self.client_address else "?"
            try:
                ln = int(self.headers.get("Content-Length", 0))
                if ln <= 0 or ln > 64 * 1024:
                    _winlog("REJECT from=%s len=%s（长度非法）" % (peer, ln))
                    self._send(400, b"BADLEN", "text/plain")
                    return
                raw = self.rfile.read(ln)
                try:
                    payload = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    _winlog("REJECT from=%s（JSON 解析失败）body=%r"
                            % (peer, raw[:120]))
                    self._send(400, b"BADJSON", "text/plain")
                    return
                if win_push(payload, peer):
                    self._send_bytes(200, b"OK", "text/plain")
                else:
                    _winlog("REJECT from=%s（字段不合法）body=%r" % (peer, raw[:200]))
                    self._send(400, b"BADDATA", "text/plain")
            except Exception:
                _winlog("REJECT from=%s（处理异常）" % peer)
                self._send(400, b"BADJSON", "text/plain")
            return
        self._send(404, b"NF", "text/plain")

    def do_GET(self):
        if self.path.startswith("/favicon"):
            self.send_response(404)
            self.end_headers()
            return
        p, _, qs = self.path.partition("?")
        # 面板 HTML 里图片用相对双缓冲名（sparkcpu_a.png）：Kindle 侧命中本地文件，
        # 浏览器经 HTTP 打开时映射回 /sparkpng/ 生成，同一份 HTML 两端都能出图。
        _m_sp = re.match(
            r"^/spark(cpu|membar|memtrend|diskread|diskwrite|netdown|netup|dgxutil|dgxtemp|wincpu|winnet)_[ab]\.png$", p)
        if _m_sp:
            p = "/sparkpng/" + _m_sp.group(1) + ".png"
        if qs.startswith("k="):
            KINDLE_K[0] = qs[2:]
            _kp = (KINDLE_K[0].split(";") + ["", "", "", "", ""])[:5]
            for _arr, _idx in ((HIST_KB, 0), (HIST_KM, 2), (HIST_KD, 3)):
                try:
                    _arr.append(float(_kp[_idx]))
                except (TypeError, ValueError):
                    _arr.append(None)
                if len(_arr) > 240:
                    _arr.pop(0)
            _hsave()
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
                 "disk": (d2.get("disk") or {}).get("usage"),
                 "dgx": (spark_snapshot().get("util") or 0)}
            import io as _io
            buf = _io.BytesIO()
            render_ring(m.get(nm) or 0).convert("RGB").save(buf, "PNG", optimize=True)
            self._send_bytes(200, buf.getvalue(), "image/png")
            return
        if p.startswith("/sparkpng/"):
            nm = p.split("/sparkpng/")[1].split(".")[0]
            # PNG 缓存：同一张图 2s 内重复请求直接返回，免重复渲染
            _cache_key = nm
            _now = time.time()
            if _PNG_CACHE.get(_cache_key, (0, b''))[0] + 2 > _now:
                png = _PNG_CACHE[_cache_key][1]
            else:
                if nm == "membar":    # 内存构成条 + Comp/Swap 折线（单图）
                    png = render_memcombo_png(collect().get("mem") or {})
                elif nm == "memcomp": # 内存构成条（大图，兼容旧页）
                    png = render_memcomp_png(collect().get("mem") or {})
                elif nm == "memtrend":  # 独立 CPU/NAND/电池温度图（复用已下载槽位）
                    _ct = _hlast(HIST_TEMP)
                    _nn = _hlast(HIST_TN)
                    _bt = _hlast(HIST_TB)
                    png = render_curves_png(
                        [(HIST_TEMP, 0, 4), (HIST_TN, 110, 3), (HIST_TB, 170, 2)],
                        h=70, font_size=28, y_max=120,
                        legend_items=[
                            ("CPU %s°C" % (("%.0f" % _ct) if _ct else "--"), 0, 4),
                            ("NAND %s°C" % (("%.0f" % _nn) if _nn else "--"), 110, 3),
                            ("电池 %s°C" % (("%.0f" % _bt) if _bt else "--"), 170, 2),
                        ])
                elif nm == "cpu":     # CPU 黑实线 + GPU 灰细线
                    cur_gpu = [v for v in HIST_GPU if v is not None]
                    gpu_cur = int(cur_gpu[-1]) if cur_gpu else 0
                    png = render_curves_png(
                        [(HIST_CPU, 0, 4), (HIST_GPU, 110, 3)],
                        h=120, font_size=40,
                        legend_items=[("CPU %d%%" % _hmax2(HIST_CPU), 0, 3),
                                      ("GPU %d%%" % gpu_cur, 110, 2)])
                elif nm == "mem":     # 内存黑实线 + Swap 灰细线（同图双线）
                    cur_sw = [v for v in HIST_SWAP if v is not None]
                    sw_cur = int(cur_sw[-1]) if cur_sw else 0
                    png = render_curves_png(
                        [(HIST_MEM, 0, 4), (HIST_SWAP, 110, 3)],
                        h=120, font_size=40,
                        legend_items=[("内存 %d%%" % _hmax2(HIST_MEM), 0, 3),
                                      ("Swap %d%%" % sw_cur, 110, 2)])
                elif nm == "kindle":  # 电池黑实线 + 内存灰细线（存储移至 Disk 卡对比）
                    kp = (KINDLE_K[0].split(";") + ["--"] * 5)[:5]
                    png = render_curves_png(
                        [(HIST_KB, 0, 3), (HIST_KM, 110, 2)],
                        legend_items=[("电池 %s%%" % kp[0], 0, 3),
                                      ("内存 %s%%" % kp[2], 110, 2)])
                elif nm == "temp":    # CPU 黑实线 + NAND 灰线 + 电池浅灰线（°C 同量纲）
                    _ct = [v for v in HIST_TEMP if v is not None]
                    _nn = [v for v in HIST_TN if v is not None]
                    _bt = [v for v in HIST_TB if v is not None]
                    png = render_curves_png(
                        [(HIST_TEMP, 0, 4), (HIST_TN, 110, 3), (HIST_TB, 170, 2)],
                        h=80, font_size=30,
                        legend_items=[
                            ("CPU %s°C" % (("%.0f" % _ct[-1]) if _ct else "--"), 0, 4),
                            ("NAND %s°C" % (("%.0f" % _nn[-1]) if _nn else "--"), 110, 3),
                            ("电池 %s°C" % (("%.0f" % _bt[-1]) if _bt else "--"), 170, 2),
                        ])
                elif nm == "diskio":  # 磁盘读黑实线 + 写灰线
                    png = render_curves_png(
                        [(HIST_IOR, 0, 4), (HIST_IOW, 110, 3)],
                        h=120, font_size=40,
                        legend_items=[("读 %.1f MB/s" % _hlast(HIST_IOR), 0, 4),
                                      ("写 %.1f MB/s" % _hlast(HIST_IOW), 110, 3)])
                elif nm == "netio":   # 网络下行黑实线 + 上行灰线
                    png = render_curves_png(
                        [(HIST_ND, 0, 4), (HIST_NU, 110, 3)],
                        h=120, font_size=40,
                        legend_items=[("↓ %.0f KB/s" % _hlast(HIST_ND), 0, 4),
                                      ("↑ %.0f KB/s" % _hlast(HIST_NU), 110, 3)])
                elif nm == "diskread":   # 磁盘读：独立上图
                    png = render_curves_png(
                        [(HIST_IOR, 0, 5)], h=48, font_size=24, compact=True,
                        legend_items=[("读 %.1f MB/s" % _hlast(HIST_IOR), 0, 5)])
                elif nm == "diskwrite":  # 磁盘写：独立下图
                    png = render_curves_png(
                        [(HIST_IOW, 110, 5)], h=48, font_size=24, compact=True,
                        legend_items=[("写 %.1f MB/s" % _hlast(HIST_IOW), 110, 5)])
                elif nm == "netdown":    # 网络下行：独立上图
                    png = render_curves_png(
                        [(HIST_ND, 0, 5)], h=48, font_size=24, compact=True,
                        legend_items=[("↓ %.0f KB/s" % _hlast(HIST_ND), 0, 5)])
                elif nm == "netup":      # 网络上行：独立下图
                    png = render_curves_png(
                        [(HIST_NU, 110, 5)], h=48, font_size=24, compact=True,
                        legend_items=[("↑ %.0f KB/s" % _hlast(HIST_NU), 110, 5)])
                elif nm == "wincpu":    # Windows CPU 黑实线 + 内存灰细线
                    png = render_curves_png(
                        [(HIST_WCPU, 0, 4), (HIST_WMEM, 110, 3)],
                        h=120, font_size=40,
                        legend_items=[("CPU %d%%" % _hmax2(HIST_WCPU), 0, 4),
                                      ("内存 %d%%" % _hmax2(HIST_WMEM), 110, 3)])
                elif nm == "winnet":    # Windows 网络下行黑实线 + 上行灰细线
                    png = render_curves_png(
                        [(HIST_WNETD, 0, 4), (HIST_WNETU, 110, 3)],
                        h=120, font_size=40,
                        legend_items=[("↓ %.0f KB/s" % _hlast(HIST_WNETD), 0, 4),
                                      ("↑ %.0f KB/s" % _hlast(HIST_WNETU), 110, 3)])
                elif nm == "dgxutil":    # DGX GPU 利用率：黑粗实线（与 CPU 同色）
                    png = render_curves_png(
                        [(HIST_DGXU, 0, 5)], h=40, font_size=22, compact=True, y_max=100,
                        legend_items=[("GPU %s%%" % _dgx_val(HIST_DGXU, "%d"), 0, 5)])
                elif nm == "dgxtemp":    # DGX GPU 温度：灰细虚线（与 NAND 同色）
                    png = render_curves_png(
                        [(HIST_DGXT, 110, 2)], h=40, font_size=22, compact=True, y_max=120,
                        legend_items=[("温度 %s°C" % _dgx_val(HIST_DGXT, "%d"), 110, 2)])
                else:                 # disk 黑实线 + Kindle 存储灰细线（两端存储对比）
                    kp = (KINDLE_K[0].split(";") + ["--"] * 5)[:5]
                    png = render_curves_png(
                        [(HIST_DISK, 0, 3), (HIST_KD, 110, 2)],
                        legend_items=[("磁盘 %d%%" % _hmax2(HIST_DISK), 0, 3),
                                      ("K存储 %s%%" % kp[3], 110, 2)])
                _PNG_CACHE[_cache_key] = (_now, png)
            self._send_bytes(200, png, "image/png")
            return
        if p.startswith("/probe.js"):
            self._send(200, SCRIPT_JS, "application/javascript")
            return
        if p.startswith("/spark.json"):
            self._send(200, json.dumps(spark_payload(), ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if p.startswith("/laytest"):
            self._send(200, LAYTEST_HTML, "text/html; charset=utf-8")
            return
        if p.startswith("/history"):
            html = render_history()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if p.startswith("/lan.txt"):
            self._send(200, mac_lan_ip() + "\n", "text/plain; charset=utf-8")
            return
        if p.startswith("/win/"):
            # Windows 采集脚本下载（白名单，不做任意路径读，防路径穿越）
            fname = p[len("/win/"):]
            if fname not in WIN_FILES:
                self._send(404, b"NF", "text/plain")
                return
            try:
                with open(os.path.join(BASE, fname), "rb") as f:
                    data = f.read()
                self._send_bytes(200, data, "text/plain; charset=utf-8")
            except OSError:
                self._send(404, b"NF", "text/plain")
            return
        if p.startswith("/vp"):
            try:
                with open("/tmp/panel-vp.log", "a") as f:
                    f.write(self.path + "\n")
            except Exception:
                pass
            self._send_bytes(200, b"", "text/plain")
            return
        if p.startswith("/panel.html"):
            try:
                # Kindle 的 index.html 是 fetchloop 用 curl 抓的（User-Agent=curl/…），
                # 屏幕 1072×1448 且 overflow:hidden 不滚动 → 收掉 WIN 卡的曲线图，只留文字行。
                _ua = (self.headers.get("User-Agent") or "").lower()
                self._send(200, render_panel(kindle="curl" in _ua),
                           "text/html; charset=utf-8")
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
