#!/usr/bin/env python3
# 唤醒屏幕（powerd activity）→ 启动面板 → 截 framebuffer
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
    # 唤醒并保活 60 秒
    print(cmd(s, 'lipc-set-prop com.lab126.powerd activity 60 2>&1; echo WAKE_END', 3))
    time.sleep(3)
    print(cmd(s, 'lipc-set-prop com.lab126.appmgrd start app://com.kindle.macpanel 2>&1; echo APP_END', 5))
    time.sleep(8)
    print(cmd(s, 'gzip -c /dev/fb0 > /tmp/fb.gz && curl -s --noproxy "*" -T /tmp/fb.gz '
          '"http://192.168.15.201:18866/fb0" -w "%{http_code}"; echo UP_END', 8))
    s.close()


if __name__ == '__main__':
    main()
