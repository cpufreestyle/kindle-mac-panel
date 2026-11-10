#!/bin/bash
# 天气采集 — launchd 每 30 分钟调用，写 /tmp/kindle-wx.json
# 城市从 city.txt 读取（白名单字符过滤后拼接，域名协议固定）
CITY=$(cat $(dirname "$0")/city.txt 2>/dev/null | tr -cd 'A-Za-z0-9 -' | head -c 40)
CITY=${CITY:-Shanghai}
OUT=/tmp/kindle-wx.json

python3 - "$CITY" "$OUT" <<'PYEOF'
import sys, json, re, urllib.request
city, out = sys.argv[1], sys.argv[2]
city = re.sub(r"[^A-Za-z0-9 \-]", "", city)[:40] or "Shanghai"
txt, rng, sun = "", "", ""
try:
    u = "https://wttr.in/" + city + "?format=j1"
    j = json.loads(urllib.request.urlopen(u, timeout=12).read())
    cur = j["current_condition"][0]
    WMO = {113: "晴", 116: "多云", 119: "阴", 122: "阴", 143: "雾", 248: "雾",
           176: "阵雨", 263: "毛雨", 293: "小雨", 299: "大雨", 302: "雨", 308: "暴雨",
           200: "雷雨", 386: "雷雨", 323: "小雪", 326: "雪", 332: "中雪", 338: "大雪"}
    desc = WMO.get(int(cur["weatherCode"]), "")
    txt = desc + " " + cur["temp_C"] + "°C"
    w0 = j["weather"][0]
    rng = w0["mintempC"] + "~" + w0["maxtempC"] + "°"
    ast = w0["astronomy"][0]
    sun = "日出" + ast["sunrise"] + " 落" + ast["sunset"]
except Exception:
    pass
json.dump({"txt": txt, "range": rng, "sun": sun}, open(out, "w"), ensure_ascii=False)
PYEOF
