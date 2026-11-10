#!/usr/bin/env python3
# fetchloop v4：面板 HTML + 屏保图双拉取；屏保图变化时 mntroot rw 写入 blanket 屏保目录
# 部署前自动备份原 bg_ss00.png
import socket
import time
import base64

HOST = ('192.168.15.244', 23)
D = '/var/local/mesquite/com.kindle.macpanel'
SS_DIR = '/usr/share/blanket/screensaver'

FETCHLOOP_V4 = "\n".join([
    "#!/bin/sh",
    "# v5: 拉面板(?k=电池;温度;内存%;存储%;充电中) + 拉屏保图；图变化时 rw 写入屏保目录",
    'D=/var/local/mesquite/com.kindle.macpanel',
    'M="http://192.168.1.191:18866"',
    'SS=' + SS_DIR + '/bg_ss00.png',
    'SSM=/mnt/us/usbnet/ss.md5',
    "while true; do",
    "  B=$(cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1)",
    "  T=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -rn | head -1)",
    "  MU=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf \"%d\",(t-a)*100/t}' /proc/meminfo 2>/dev/null)",
    "  DU=$(df -k /mnt/us | tail -1 | awk '{printf \"%d\",$3*100/$2}')",
    "  CS=$(cat /sys/class/power_supply/bd71827_ac/online 2>/dev/null | head -1)",
    '  curl -s --max-time 8 "$M/panel.html?k=${B:-na};${T:-na};${MU:-na};${DU:-na};${CS:-0}" -o $D/index.new',
    "  if [ $? -eq 0 ] && [ -s $D/index.new ]; then",
    "    mv -f $D/index.new $D/index.html",
    "  else",
    "  printf '<!DOCTYPE html><html><head><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"20\"></head><body style=\"margin:0;padding:140px 50px;font-family:sans-serif\"><h1 style=\"font-size:56px;font-weight:bold\">连接已断开</h1><p style=\"font-size:36px;color:#555\">面板数据停更 - 断于 %s</p></body></html>' \"$(date +%H:%M:%S)\" > $D/index.new",
    "    mv -f $D/index.new $D/index.html",
    "  fi",
    '  curl -s --max-time 10 "$M/screensaver.png" -o /mnt/us/usbnet/ss.png',
    "  if [ -s /mnt/us/usbnet/ss.png ]; then",
    "    NM=$(md5sum /mnt/us/usbnet/ss.png | cut -d' ' -f1)",
    "    OM=$(cat $SSM 2>/dev/null)",
    '    if [ "$NM" != "$OM" ] && [ -n "$NM" ]; then',
    "      echo $NM > $SSM",
    "      mntroot rw 2>/dev/null",
    "      cp /mnt/us/usbnet/ss.png $SS && sync",
    "      mntroot ro 2>/dev/null",
    "    fi",
    "  fi",
    "  sleep 15",
    "done",
])


def rq(s):
    d = b''
    s.settimeout(2)
    while True:
        try:
            c = s.recv(65536)
            if not c:
                break
            d += c
        except socket.timeout:
            break
    return d


def push_b64(s, content, remote):
    b64 = base64.b64encode(content.encode()).decode()
    s.sendall(b"rm -f /tmp/d.b64\n")
    time.sleep(0.3)
    for i in range(0, len(b64), 150):
        s.sendall(("echo -n '" + b64[i:i + 150] + "' >> /tmp/d.b64\n").encode())
        time.sleep(0.18)
    s.sendall(("base64 -d /tmp/d.b64 > " + remote + "\n").encode())
    time.sleep(0.4)
    s.sendall(b"rm -f /tmp/d.b64\n")
    time.sleep(0.25)


def cmd(s, c, wait=1.5):
    s.sendall((c + '\n').encode())
    time.sleep(wait)
    return rq(s).decode('utf-8', 'replace')


def main():
    s = socket.create_connection(HOST, timeout=8)
    rq(s)
    cmd(s, '', 0.5)

    # 1) 备份原屏保图（幂等）
    print('--- 备份原 bg_ss00.png ---')
    print(cmd(s, '[ -f /mnt/us/usbnet/ss00.orig.bak ] || '
          'cp ' + SS_DIR + '/bg_ss00.png /mnt/us/usbnet/ss00.orig.bak; '
          'ls -la /mnt/us/usbnet/ss00.orig.bak', wait=2.5))

    # 2) 推 fetchloop v4
    print('--- 部署 fetchloop v4 ---')
    push_b64(s, FETCHLOOP_V4, D + '/fetchloop.sh')
    print(cmd(s, 'chmod +x ' + D + '/fetchloop.sh'))

    # 3) 重启 loop
    print(cmd(s, 'pkill -f fetchloop.sh 2>/dev/null; sleep 1'))
    print(cmd(s, 'nohup ' + D + '/fetchloop.sh >/dev/null 2>&1 & echo V4_STARTED', wait=1.5))
    print(cmd(s, 'pgrep -f fetchloop.sh | wc -l'))

    # 4) 等首轮拉取 + 写屏保
    print('--- 等 25s 验证屏保写入 ---')
    time.sleep(25)
    print(cmd(s, 'ls -la ' + SS_DIR + '/bg_ss00.png /mnt/us/usbnet/ss.png', wait=2))
    print(cmd(s, 'cat /mnt/us/usbnet/ss.md5'))
    s.close()


if __name__ == '__main__':
    main()
