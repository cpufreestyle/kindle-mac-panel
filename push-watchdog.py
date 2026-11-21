#!/usr/bin/env python3
# 分块 base64 推送本地脚本到设备（默认 watchdog，可传参：push-watchdog.py <本地> <远程>）
import base64
import os
import socket
import sys
import time

HOST = ('192.168.15.244', 23)
# 默认推送同目录下的 watchdog-v33.sh —— 用脚本自身位置推导，目录搬家后不用改
_BASE = os.path.dirname(os.path.abspath(__file__))
LOCAL = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_BASE, 'watchdog-v33.sh')
REMOTE = sys.argv[2] if len(sys.argv) > 2 else '/mnt/us/usbnet/watchdog.sh'


def telnet(cmds, waits):
    s = socket.create_connection(HOST, timeout=8)
    s.settimeout(4)
    try:
        s.recv(65536)
    except Exception:
        pass
    outs = []
    for c, w in zip(cmds, waits):
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


data = open(LOCAL, 'rb').read()
b64 = base64.b64encode(data).decode('ascii')
chunks = [b64[i:i + 650] for i in range(0, len(b64), 650)]
cmds = ['rm -f /tmp/wd.b64']
cmds += ["echo '%s' >> /tmp/wd.b64" % c for c in chunks]
cmds += [
    'base64 -d /tmp/wd.b64 > ' + REMOTE + '.new',
    'wc -c ' + REMOTE + '.new; sh -n ' + REMOTE + '.new && echo DEV_SYNTAX_OK',
    'mv ' + REMOTE + '.new ' + REMOTE + ' && wc -c ' + REMOTE + '; echo PUSHED',
]
waits = [1.5] + [1.2] * len(chunks) + [3, 4, 4]
outs = telnet(cmds, waits)
for o in outs[-3:]:
    for l in o.splitlines():
        t = l.strip()
        if any(k in t for k in ('DEV_SYNTAX_OK', 'PUSHED', 'SYNTAX', 'watchdog.sh')):
            print(t)
print('LOCAL_BYTES', len(data))
