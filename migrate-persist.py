#!/usr/bin/env python3
# 治本：fetchloop/panel 数据迁移到 /mnt/us（重启持久），恢复 index，修正 cron
import socket
import time

HOST = ('192.168.15.244', 23)

FETCHLOOP_PERSIST = r'''#!/bin/sh
# fetchloop v9（持久版+多 IP fallback+周期全刷）：USB IP 优先（Mac 侧持久化），Wi-Fi IP 兜底
D=/var/local/mesquite/com.kindle.macpanel
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
    eips -s w=758,h=1024 -f 2>/dev/null
  fi
  sleep 3
done
'''

CRON_NEW = ("* * * * * /mnt/us/usbnet/watchdog.sh\n"
            "* * * * * pgrep -f 'usbnet/fetchloop' >/dev/null || "
            "nohup /mnt/us/usbnet/fetchloop.sh >/dev/null 2>&1 &\n"
            "* * * * * cp /mnt/us/usbnet/panel.html "
            "/var/local/mesquite/com.kindle.macpanel/index.html 2>/dev/null\n")


def rq(s):
    d = b''
    s.settimeout(3)
    while True:
        try:
            c = s.recv(65536)
            if not c:
                break
            d += c
        except socket.timeout:
            break
    return d


def cmd(s, c, wait=3.0):
    s.sendall((c + '\n').encode())
    time.sleep(wait)
    return rq(s).decode('utf-8', 'replace')


def push_b64(s, content, remote):
    import base64
    b64 = base64.b64encode(content.encode()).decode()
    s.sendall(b"rm -f /tmp/mv.b64\n")
    time.sleep(0.3)
    for i in range(0, len(b64), 150):
        s.sendall(("echo -n '" + b64[i:i + 150] + "' >> /tmp/mv.b64\n").encode())
        time.sleep(0.15)
    s.sendall(("base64 -d /tmp/mv.b64 > " + remote + "\n").encode())
    time.sleep(0.4)
    s.sendall(b"rm -f /tmp/mv.b64\n")
    time.sleep(0.3)


def main():
    s = socket.create_connection(HOST, timeout=8)
    rq(s)
    cmd(s, '', 0.3)

    print('--- 1. 拉最新面板到 /mnt/us ---')
    print(cmd(s, 'curl -s --max-time 8 "http://192.168.1.191:18866/panel.html?k=99;30000;55;27;1" '
          '-o /mnt/us/usbnet/panel.html; wc -c /mnt/us/usbnet/panel.html; echo P1_END', 5).strip().splitlines()[1:])

    print('--- 2. fetchloop v6 持久版 ---')
    push_b64(s, FETCHLOOP_PERSIST, '/mnt/us/usbnet/fetchloop.sh')
    print(cmd(s, 'chmod +x /mnt/us/usbnet/fetchloop.sh; echo P2_END', 1.5))

    print('--- 3. 恢复 index.html + cron 重建 ---')
    print(cmd(s, 'mntroot rw; '
          'cat /etc/crontab/root | grep -v "usbnet/watchdog\\|fetchloop\\|macpanel" > /tmp/cr.new; '
          'echo done1', 3).strip().splitlines()[1:])
    push_b64(s, CRON_NEW, '/tmp/cr.add')
    print(cmd(s, 'cat /tmp/cr.add >> /tmp/cr.new; '
          'cp /tmp/cr.new /etc/crontab/root; '
          'cp /mnt/us/usbnet/panel.html /var/local/mesquite/com.kindle.macpanel/index.html; '
          'initctl restart cron; mntroot ro; echo P3_END', 5).strip().splitlines()[1:])

    print('--- 4. 启动 fetchloop + 重启应用 ---')
    print(cmd(s, 'pkill -f "usbnet/fetchloop" 2>/dev/null; '
          'nohup /mnt/us/usbnet/fetchloop.sh >/dev/null 2>&1 & echo P4_END', 2).strip().splitlines()[1:])
    print(cmd(s, 'killall mesquite 2>/dev/null; sleep 2; '
          'lipc-set-prop com.lab126.appmgrd start app://com.kindle.macpanel; echo P4B_END', 7).strip().splitlines()[1:])

    print('--- 5. 验证 ---')
    print(cmd(s, 'pgrep -f "usbnet/fetchloop" | wc -l; '
          'grep -c "khead2" /var/local/mesquite/com.kindle.macpanel/index.html; '
          'grep -c "下行" /var/local/mesquite/com.kindle.macpanel/index.html; echo P5_END', 3).strip().splitlines()[1:])

    s.close()


if __name__ == '__main__':
    main()
