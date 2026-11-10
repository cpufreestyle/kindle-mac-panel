#!/usr/bin/env python3
# 立即刷新设备端面板（拉最新版 + relaunch 应用），不等 fetchloop 轮询
import socket
import time

HOST = ('192.168.15.244', 23)
D = '/var/local/mesquite/com.kindle.macpanel'

# 与 fetchloop v3 相同的采集逻辑（电池;温度m°C;内存占用%;存储占用%）
FETCH_CMD = (
    'B=$(cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1); '
    'T=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -rn | head -1); '
    'MU=$(awk \'/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%d",(t-a)*100/t}\' '
    '/proc/meminfo 2>/dev/null); '
    'DU=$(df -k /mnt/us | tail -1 | awk \'{printf "%d",$3*100/$2}\'); '
    'curl -s --max-time 8 "http://192.168.1.191:18866/panel.html?'
    'k=${B:-na};${T:-na};${MU:-na};${DU:-na}" -o ' + D + '/index.new '
    '&& mv -f ' + D + '/index.new ' + D + '/index.html && echo FETCH_OK'
)


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


def cmd(s, c, wait=1.5):
    s.sendall((c + '\n').encode())
    time.sleep(wait)
    return rq(s).decode('utf-8', 'replace')


def main():
    s = socket.create_connection(HOST, timeout=8)
    rq(s)
    print('--- 拉最新版 ---')
    print(cmd(s, FETCH_CMD, wait=4))
    print('--- 温和重启应用（不触发 appmgrd 错误弹窗） ---')
    print(cmd(s, 'lipc-set-prop com.lab126.appmgrd stop app://com.kindle.macpanel; '
          'sleep 2; lipc-set-prop com.lab126.appmgrd start app://com.kindle.macpanel; '
          'echo RELAUNCHED', wait=6))
    print('--- 验证新指纹（toppro=顶进程排行版） ---')
    print(cmd(s, 'grep -c toppro ' + D + '/index.html', wait=1.5))
    s.close()


if __name__ == '__main__':
    main()
