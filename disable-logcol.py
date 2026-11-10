#!/usr/bin/env python3
# 终极：禁用 demd（诊断事件管理 — 崩溃收集+弹窗真源）+ 清空全部 .sdr 归档
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

    print('--- 确认 demd ---')
    print(cmd(s, 'head -6 /etc/upstart/demd.conf; echo D_END', 2.5).strip().splitlines()[1:])
    print('--- 停服务 + 禁自启 ---')
    print(cmd(s, 'initctl stop demd 2>/dev/null; echo S=$?', 3).strip().splitlines()[1:])
    print(cmd(s, 'mntroot rw; '
          'if [ -f /etc/upstart/demd.conf ] && [ ! -f /etc/upstart/demd.conf.disabled ]; then '
          'mv /etc/upstart/demd.conf /etc/upstart/demd.conf.disabled; echo MOVED; fi; '
          'mntroot ro; echo D1_END', 4).strip().splitlines()[1:])
    print('--- 清空全部 .sdr 崩溃归档 ---')
    print(cmd(s, 'rm -rf /mnt/us/documents/mesquite_*_crash_*; '
          'echo CLEANED; ls /mnt/us/documents/ | grep -c crash; echo D2_END', 6).strip().splitlines()[1:])
    print('--- 验证 ---')
    print(cmd(s, 'ls /etc/upstart/ | grep -iE "demd|last_debug"; echo V_END', 2).strip().splitlines()[1:])
    s.close()


if __name__ == '__main__':
    main()
