#!/usr/bin/env python3
# 通用 telnet 命令器：python3 telnet-cmd.py "命令1" "命令2" ...
import socket
import sys
import time

HOST = ('192.168.15.244', 23)


def run(cmds, waits=None):
    s = socket.create_connection(HOST, timeout=8)
    s.settimeout(2)
    try:
        s.recv(65536)
    except Exception:
        pass
    outs = []
    for i, c in enumerate(cmds):
        w = (waits[i] if waits and i < len(waits) else 2.5)
        s.sendall((c + '\n').encode())
        time.sleep(w)
        d = b''
        try:
            while True:
                x = s.recv(65536)
                if not x:
                    break
                d += x
        except socket.timeout:
            pass
        outs.append(d.decode('utf-8', 'replace'))
    s.close()
    return outs


if __name__ == '__main__':
    for o in run(sys.argv[1:]):
        print(o.strip())
        print('---')
