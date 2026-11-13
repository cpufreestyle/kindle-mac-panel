#!/usr/bin/env python3
# fetchloop 全刷参数修正（758x1024→1088x1536）+ 立即全屏全刷
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
    print('A(fetchloop 参数修正):', cmd(
        s, 'sed -i "s|w=758,h=1024|w=1088,h=1536|" /mnt/us/usbnet/fetchloop.sh; '
        'grep -c "w=1088,h=1536" /mnt/us/usbnet/fetchloop.sh; echo A_END', 2.5).strip().splitlines()[1:])
    print('B(fetchloop 重启):', cmd(
        s, 'pkill -f "usbnet/fetchloop"; sleep 1; '
        'nohup /mnt/us/usbnet/fetchloop.sh >/dev/null 2>&1 & echo R', 2).strip().splitlines()[1:])
    print('C(立即全屏全刷):', cmd(
        s, 'eips -s w=1088,h=1536 -f; echo C_END', 4).strip().splitlines()[1:])
    s.close()


if __name__ == '__main__':
    main()
