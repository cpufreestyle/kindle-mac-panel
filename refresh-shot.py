#!/usr/bin/env python3
# killall 清 mesquite 页面缓存 + 重启 + 重截屏（一次性操作）
import socket
import time

HOST = ('192.168.15.244', 23)


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


def main():
    s = socket.create_connection(HOST, timeout=8)
    rq(s)
    cmd(s, '', 0.3)

    # fetchloop 拉最新（确保 index 是新 CSS）
    print(cmd(s, '/var/local/mesquite/com.kindle.macpanel/refresh.sh >/dev/null 2>&1; echo PULLED', 4))
    # killall 清缓存（会弹一次错误框 — 用户点掉即可）+ 重启
    print(cmd(s, 'killall mesquite 2>/dev/null; sleep 2; '
          'lipc-set-prop com.lab126.appmgrd start app://com.kindle.macpanel; echo RESTARTED', 7))
    s.close()

    time.sleep(15)
    # framebuffer 截屏
    s = socket.create_connection(HOST, timeout=8)
    rq(s)
    cmd(s, '', 0.3)
    print(cmd(s, 'gzip -c /dev/fb0 > /tmp/fb.gz && curl -s --noproxy "*" -T /tmp/fb.gz '
          '"http://192.168.15.201:18866/fb0" -w "%{http_code}"; echo UP_END', 8))
    s.close()


if __name__ == '__main__':
    main()
