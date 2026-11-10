#!/bin/bash
# 每日摘要 — 每天 08:05 推送昨日回顾（Bark + Mac 通知）
/usr/bin/python3 - <<'PYEOF'
import json, subprocess
BASE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))

hist = []
try:
    hist = json.load(open(__import__("os").path.join(BASE, "history.json")))
except Exception:
    pass
tt = [(h[0], h[1]) for h in hist if len(h) > 1 and h[1] is not None]
cc = [(h[0], h[2]) for h in hist if len(h) > 2 and h[2] is not None]
sv = [(h[0], h[3]) for h in hist if len(h) > 3 and h[3] is not None]
parts = []
if cc:
    cm = max(cc, key=lambda x: x[1])
    parts.append("CPU峰 %d%%@%s" % (cm[1], cm[0]))
if tt:
    tm = max(tt, key=lambda x: x[1])
    parts.append("温峰 %d°@%s" % (tm[1], tm[0]))
if sv:
    sm = max(min(v, 30.0) for v, _ in [(x[1], 0) for x in sv])
    parts.append("Swap峰 %.1fG" % sm)
msg = " · ".join(parts) if parts else "昨日无采样数据"

# Bark 手机推送（bark-url.txt 配置后激活）
try:
    burl = open(__import__("os").path.join(BASE, "bark-url.txt")).read().strip()
    if burl.startswith("http"):
        import urllib.parse, urllib.request as ur
        op = ur.build_opener(ur.ProxyHandler({}))
        u = (burl.rstrip("/") + "/" + urllib.parse.quote("昨日摘要") + "/" +
             urllib.parse.quote(msg) + "?group=MacMonitor")
        op.open(u, timeout=6)
except Exception:
    pass

# Mac 本机通知
try:
    subprocess.run(["osascript", "-e",
                    'display notification "%s" with title "Mac 昨日摘要"' % msg.replace('"', "'")],
                   timeout=10)
except Exception:
    pass
print("DAILY:", msg)
PYEOF
