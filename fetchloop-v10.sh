#!/bin/sh
# fetchloop v12（v11 + 多网段自发现）：Mac 的 en0 IP 随网络切换（192.168.1.x / 192.168.15.x），
# 写死单个 LAN 探针会在换网后失联，改为依次探测多个候选地址并回写缓存。
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
  CACHE=/mnt/us/usbnet/mac-ip.txt
  PROBES="192.168.15.201 192.168.1.136 192.168.1.191 192.168.1.112 192.168.1.224 $(cat $CACHE 2>/dev/null)"
  LAN=""
  for PB in $PROBES; do
    R=$(curl -s --max-time 1 "http://$PB:18866/lan.txt")
    if [ -n "$R" ]; then
      LAN=$R
      echo "$R" > $CACHE
      break
    fi
  done
  [ -z "$LAN" ] && LAN=$(cat $CACHE 2>/dev/null)
  if ifconfig usb0 2>/dev/null | grep -q "RUNNING"; then
    TARGETS="192.168.15.201 $LAN 192.168.1.136 192.168.1.191 192.168.1.112"
  else
    TARGETS="$LAN 192.168.1.136 192.168.1.191 192.168.1.112 192.168.15.201"
  fi
  OK=""
  for TGT in $TARGETS; do
    if curl -sf --max-time 6 "http://$TGT:18866/panel.html?$K" -o $P.new \
       && [ -s $P.new ] && grep -q DOCTYPE $P.new; then
      mv -f $P.new $P
      OK=$TGT
      break
    fi
  done
  if [ -n "$OK" ]; then
    cp $P $D/index.html
    cp $P $DOC/index.html
    if curl -sf --max-time 6 "http://$OK:18866/spark.json" -o $D/spark.json.new \
       && [ -s $D/spark.json.new ]; then
      mv -f $D/spark.json.new $D/spark.json
      cp $D/spark.json $DOC/spark.json
    fi
    for C in cpu membar memtrend diskread diskwrite netdown netup dgxutil dgxtemp wincpu winnet; do
      if curl -sf --max-time 6 "http://$OK:18866/sparkpng/$C.png" -o $D/spark$C.new \
         && [ -s $D/spark$C.new ]; then
        mv -f $D/spark$C.new $D/spark$C.png
        cp $D/spark$C.png $DOC/spark$C.png
        cp $D/spark$C.png $D/spark${C}_a.png
        cp $D/spark$C.png $D/spark${C}_b.png
        cp $D/spark${C}_a.png $DOC/spark${C}_a.png
        cp $D/spark${C}_b.png $DOC/spark${C}_b.png
      fi
    done
    for F in script.js probe.js; do
      if curl -sf --max-time 6 "http://$OK:18866/$F" -o $D/$F.new \
         && [ -s $D/$F.new ]; then
        mv -f $D/$F.new $D/$F
        cp $D/$F $DOC/$F
      fi
    done
    touch $D/invalidatedTimeStamp
    if [ $((N % 4)) -eq 0 ]; then
      if curl -sf --max-time 10 "http://$OK:18866/screensaver.png" -o /mnt/us/usbnet/ss.new \
         && [ -s /mnt/us/usbnet/ss.new ]; then
        mv -f /mnt/us/usbnet/ss.new /mnt/us/usbnet/ss.png
      fi
    fi
    if [ $((N % 4)) -eq 0 ]; then
      for R in cpu gpu ram disk; do
        if curl -sf --max-time 6 "http://$OK:18866/ring/$R.png" -o $D/ring_$R.new \
           && [ -s $D/ring_$R.new ]; then
          mv -f $D/ring_$R.new $D/ring_$R.png
        fi
      done
    fi
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
# 周期全刷：8 秒一轮时，每 15 轮约 2 分钟清一次残影
  N=$((N+1))
  if [ $((N % 15)) -eq 0 ]; then
    eips -s w=1088,h=1536 -f 2>/dev/null
  fi
  sleep 8
done
