#!/usr/bin/env python3
"""snap.py — 抓 Kindle 屏幕存成 PNG。

用途：改完面板布局要在墨水屏真机上验收，光看 HTML 不够。

帧缓冲格式（2026-11-21 实测 /sys/class/graphics/fb0/）：
    virtual_size = 1088,6144   bits_per_pixel = 8   stride = 1088
    → 8bpp 灰度，4 页滚动缓冲（每页 1536 高），当前显示页在 page0
    注意：不是 32bpp —— 按 32bpp 解会得到满屏横向乱码。

用法：
    python3 snap.py                       # 抓当前屏幕 → /tmp/kindle-snap.png
    python3 snap.py out.png               # 指定输出
    python3 snap.py --relaunch            # 先重启应用再抓
    python3 snap.py --settle 30           # 抓前等 30 秒（e-ink 刷完）
"""
import gzip
import os
import socket
import subprocess
import sys
import time

HOST = ('192.168.15.244', 23)
MAC_PUSH = 'http://192.168.15.201:18866/fb0'
FB_W, FB_H, PAGE_H = 1088, 6144, 1536
LOCAL_FB = '/tmp/fb.raw'


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


def capture():
    """Kindle 上 gzip /dev/fb0 → POST 到 Mac → 落地 /tmp/fb.raw"""
    if os.path.exists(LOCAL_FB):
        os.unlink(LOCAL_FB)
    cmd = ('eips -s w=1088,h=1536 -f; sleep 2; '
           'gzip -c /dev/fb0 > /tmp/fb.gz; '
           'curl -s --noproxy "*" -T /tmp/fb.gz "%s" -w "HTTP:%%{http_code}"; '
           'echo SNAPDONE' % MAC_PUSH)
    out = telnet([cmd], [12])
    txt = ''.join(out)
    if 'HTTP:200' not in txt:
        print('推送失败：%s' % ' '.join(txt.split())[-200:])
        return False
    return os.path.exists(LOCAL_FB) and os.path.getsize(LOCAL_FB) > 1000


def decode(src, dst):
    """8bpp 灰度 → PNG（取 page0，即当前显示页）

    只依赖 PIL —— /usr/bin/python3 有 PIL 但没 numpy，默认 python3 才两个都有。
    """
    try:
        from PIL import Image
    except ImportError:
        print('需要 PIL：pip3 install --user pillow')
        return False
    with gzip.open(src, 'rb') as f:
        raw = f.read()
    need = FB_W * FB_H
    if len(raw) < need:
        print('帧缓冲字节不足：%d < %d' % (len(raw), need))
        return False
    img = Image.frombytes('L', (FB_W, FB_H), raw[:need])
    img.crop((0, 0, FB_W, PAGE_H)).save(dst)
    # 顺带看后面几页有没有内容（多页应用会用到）
    for i in range(1, FB_H // PAGE_H):
        pg = img.crop((0, i * PAGE_H, FB_W, (i + 1) * PAGE_H))
        if pg.getextrema()[0] < 200:
            pg.save(dst.replace('.png', '-p%d.png' % i))
            print('  page%d 也有内容，另存了' % i)
    return True


def main():
    args = sys.argv[1:]
    out = 'out.png'
    relaunch = '--relaunch' in args
    settle = 30
    if '--settle' in args:
        i = args.index('--settle')
        settle = int(args[i + 1])
    for a in args:
        if a.endswith('.png'):
            out = a
    if relaunch:
        print('重启应用…')
        telnet(['lipc-set-prop com.lab126.appmgrd stop app://com.kindle.macpanel; '
                'sleep 3; lipc-set-prop com.lab126.appmgrd start app://com.kindle.macpanel; '
                'echo RELAUNCHED'], [6])
    if settle:
        print('等 %ds 让 e-ink 刷完…' % settle)
        time.sleep(settle)
    print('抓帧缓冲…')
    if not capture():
        print('抓取失败')
        return 1
    print('解码 → %s' % out)
    if not decode(LOCAL_FB, out):
        return 1
    print('完成：%s (%d 字节)' % (out, os.path.getsize(out)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
