#!/usr/bin/env python3
"""一键体检：Mac 服务/采集链路/设备端/屏保 九项端到端自检"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import os
BASE = os.path.dirname(os.path.abspath(__file__))

OK, BAD = "✓", "✗"
results = []


def check(name, ok, detail=""):
    results.append((OK if ok else BAD, name, detail))


# 1. Mac 主服务（绕系统代理直连——系统代理 1082 会劫持 localhost 请求致断连）
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ok1, detail1, body = False, "", None
for _ in range(2):
    try:
        body = opener.open("http://127.0.0.1:18866/panel.html", timeout=10).read().decode()
        ok1, detail1 = True, "%d 字节" % len(body)
        break
    except Exception as e:
        detail1 = str(e)[:50]
        time.sleep(3)
check("Mac 服务 :18866", ok1, detail1)
if not ok1:
    print("Kindle 监控仪表 体检报告 " + time.strftime("%H:%M:%S"))
    print("✗ Mac 服务 :18866 未运行 —", detail1)
    sys.exit(1)

# 2. 传感器新鲜度
try:
    age = time.time() - os.path.getmtime("/tmp/kindle-sensors.json")
    check("传感器链路(30s)", age < 120, "年龄 %ds" % age)
except Exception as e:
    check("传感器链路(30s)", False, str(e)[:50])

# 3. 天气
try:
    w = json.load(open("/tmp/kindle-wx.json"))
    check("天气", bool(w.get("txt")), w.get("txt", ""))
except Exception:
    check("天气", False, "无数据")

# 4. 24h 历史
try:
    n = len(json.load(open(os.path.join(BASE, "history.json"))))
    check("24h 历史落盘", n > 0, "%d 点" % n)
except Exception:
    check("24h 历史落盘", False, "无文件")

# 4b. Mac 磁盘（ENOSPC 波峰会瘫服务——重点监控项）
import shutil
try:
    fr = shutil.disk_usage("/System/Volumes/Data").free / 1024 ** 3
    check("Mac 磁盘余量", fr > 20, "剩 %.0fG" % fr)
except Exception as e:
    check("Mac 磁盘余量", False, str(e)[:50])

# 5. 心跳
try:
    hb = float(open("/tmp/kindle-kheartbeat").read().strip())
    age = time.time() - hb
    check("设备心跳(15s)", age < 120, "延迟 %ds" % age)
except Exception:
    check("设备心跳(15s)", False, "无心跳")

# 6. 设备 telnet
try:
    s = socket.create_connection(("192.168.15.244", 23), timeout=6)
    s.settimeout(1.5)
    try:
        s.recv(4096)
    except Exception:
        pass
    s.sendall(b"pgrep -f fetchloop.sh | wc -l; ls -l /var/local/mesquite/com.kindle.macpanel/index.html | awk '{print $5}'; echo EB\n")
    time.sleep(2)
    d = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            d += c
    except Exception:
        pass
    s.close()
    out = d.decode("utf-8", "replace")
    lines = [l.strip() for l in out.splitlines() if l.strip().isdigit()]
    loop_n = lines[0] if lines else "0"
    check("设备 fetchloop", loop_n == "1", "进程 %s" % loop_n)
    sz = [l.strip() for l in out.splitlines() if l.strip().isdigit()]
    check("设备 index.html", len(sz) > 1 and int(sz[1]) > 3000, "%s 字节" % (sz[1] if len(sz) > 1 else "?"))
except Exception as e:
    check("设备 telnet/fetchloop", False, str(e)[:50])

# 7. 屏保文件
try:
    s = socket.create_connection(("192.168.15.244", 23), timeout=6)
    s.settimeout(1.5)
    try:
        s.recv(4096)
    except Exception:
        pass
    s.sendall(b"md5sum /usr/share/blanket/screensaver/bg_ss00.png /mnt/us/usbnet/ss.png 2>/dev/null | awk '{print $1}' | uniq | wc -l; echo SB\n")
    time.sleep(2)
    d = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            d += c
    except Exception:
        pass
    s.close()
    out = [l.strip() for l in d.decode("utf-8", "replace").splitlines() if l.strip().isdigit()]
    check("屏保图已部署", out and out[0] == "1", "md5 一致" if out and out[0] == "1" else "不一致/未部署")
except Exception as e:
    check("屏保图已部署", False, str(e)[:50])

# 8. 告警历史（30min 内触发过的告警——历史事实，心跳等可能已恢复）
try:
    a = json.load(open("/tmp/kindle-alert.json"))
    recent = [k for k, v in a.items() if time.time() - v < 1800]
    check("30min内告警", not recent, " · ".join(recent) if recent else "无")
except Exception:
    check("30min内告警", True, "无记录")

# 9. 网络模式确认（RNDIS 通 = 非 U 盘模式）
try:
    s = socket.create_connection(("192.168.15.244", 23), timeout=6)
    s.close()
    check("网络模式(RNDIS)", True, "")
except Exception:
    check("网络模式(RNDIS)", False, "设备不可达（可能 U 盘模式，需 eject）")


def print_results():
    print("Kindle 监控仪表 体检报告 " + time.strftime("%H:%M:%S"))
    print("-" * 46)
    for mark, name, detail in results:
        print("%s %-18s %s" % (mark, name, detail))
    bad = sum(1 for m, _, _ in results if m == BAD)
    print("-" * 46)
    print("%d/%d 通过" % (len(results) - bad, len(results)))


print_results()
