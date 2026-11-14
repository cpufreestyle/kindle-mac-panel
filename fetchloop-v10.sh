#!/bin/sh
# fetchloop v10（v9 + documents 双目录同步）：USB IP 优先，Wi-Fi IP 兜底
# 双目录同步：mesquite 有两条加载路径（app 目录 / documents 目录），
# KUAL/框架重启可能走 documents——不同步会显示 Nov 1 的 v1 古董页（2026-11-14 实测）
D=/var/local/mesquite/com.kindle.macpanel
DOC=/mnt/us/documents/macpanel
P=/mnt/us/usbnet/panel.html
N=0
while true; do
  B=$(cat /sys/class/power_supply/bd71827_bat/capacity 2>/dev/null | head -1)
  T=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -rn | head -1)
  MU=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%d",(t-a)*100/t}' /proc/meminfo 2>/dev/null)
  DU=$(df -k /mnt/us | tail -1 | awk '{printf "%d",$3*100/$2}')
  CS=$(cat /sys/class/power_supply/bd71827_ac/online 2>/dev/null | head -1)
  K="k=${B:-na};${T:-na};${MU:-na};${DU:-na};${CS:-0}"
  OK=""
  for TGT in 192.168.15.201 192.168.1.112 192.168.1.191; do
    curl -s --max-time 6 "http://$TGT:18866/panel.html?$K" -o $P
    if [ -s $P ] && grep -q DOCTYPE $P; then OK=$TGT; break; fi
  done
  if [ -n "$OK" ]; then
    cp $P $D/index.html 2>/dev/null
    cp $P $DOC/index.html 2>/dev/null
    curl -s --max-time 6 "http://$OK:18866/spark.json" -o $D/spark.json
    cp $D/spark.json $DOC/spark.json 2>/dev/null
    curl -s --max-time 10 "http://$OK:18866/screensaver.png" -o /mnt/us/usbnet/ss.png
    for R in cpu gpu ram disk; do
      curl -s --max-time 6 "http://$OK:18866/ring/$R.png" -o $D/ring_$R.png
    done
  else
    echo "$(date +%m-%d\ %H:%M) all-ip-fail" >> /mnt/us/usbnet/fetchlog
    tail -50 /mnt/us/usbnet/fetchlog > /tmp/fl.tmp && mv /tmp/fl.tmp /mnt/us/usbnet/fetchlog
  fi
  NM=$(md5sum /mnt/us/usbnet/ss.png 2>/dev/null | cut -d' ' -f1)
  OM=$(cat /mnt/us/usbnet/ss.md5 2>/dev/null)
  if [ "$NM" != "$OM" ] && [ -n "$NM" ]; then
    echo $NM > /mnt/us/usbnet/ss.md5
    mntroot rw 2>/dev/null
    cp /mnt/us/usbnet/ss.png /usr/share/blanket/screensaver/bg_ss00.png && sync
    mntroot ro 2>/dev/null
  fi
  # 周期全刷：清 e-ink 局部刷新累积的残影（每 20 轮 ≈ 60s）
  N=$((N+1))
  if [ $((N % 20)) -eq 0 ]; then
    eips -s w=1088,h=1536 -f 2>/dev/null
  fi
  sleep 3
done
