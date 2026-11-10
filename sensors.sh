#!/bin/bash
# 传感器采集 — launchd 每 30s 调用，写 /tmp/kindle-sensors.json
# 采集：CPU温度(TPD最高)/GPU占用/风扇F0Ac/系统功耗PSTR+指数均值/DC电压电流/NAND·Airport·电池温度
BASE=$(cd "$(dirname "$0")" && pwd)
SMC=$BASE/smc-cli/smc
OUT=/tmp/kindle-sensors.json
AVGF=/tmp/kindle-pstr-avg

ALL=$("$SMC" list 2>/dev/null)
get() { echo "$ALL" | grep -oE "\\[$1\\][[:space:]]+[-0-9.]+" | grep -oE '[-0-9.]+$' | head -1; }

TEMP=$(echo "$ALL" | grep -oE '\[TPD[0-9A-Fa-f]\][[:space:]]+[0-9.]+' | grep -oE '[0-9.]+$' | sort -rn | head -1)
GPU=$(ioreg -r -c IOAccelerator 2>/dev/null | grep -oE '"Device Utilization %"=[0-9]+' | grep -oE '[0-9]+$' | head -1)
F0AC=$(get F0Ac)
PSTR=$(get PSTR)
ID0R=$(get ID0R)
VD0R=$(get VD0R)
TB1T=$(get TB1T)
TH0X=$(get TH0x)
TW0P=$(get TW0P)

# Average System Total：指数滑动平均（α=0.1，状态存文件跨周期）
PREV=$(cat "$AVGF" 2>/dev/null)
AVG=$(python3 -c "print(round($PREV*0.9+$PSTR*0.1,1))" 2>/dev/null)
[ -z "$AVG" ] && AVG=$PSTR
echo "$AVG" > "$AVGF"

printf '{"cpu_temp": %s, "gpu": %s, "fan": %s, "f0ac": %s, "pstr": %s, "avg_pstr": %s, "id0r": %s, "vd0r": %s, "tb1t": %s, "th0x": %s, "tw0p": %s}' \
  "${TEMP:-null}" "${GPU:-null}" "${F0AC:-null}" "${F0AC:-null}" "${PSTR:-null}" "$AVG" \
  "${ID0R:-null}" "${VD0R:-null}" "${TB1T:-null}" "${TH0X:-null}" "${TW0P:-null}" > "$OUT"
