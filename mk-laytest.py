#!/usr/bin/env python3
# 布局方案对比测试页：一次截图筛出 mesquite 支持的行布局写法
import os
import sys

# 同目录 —— 用脚本自身位置推导，目录搬家后不用改
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import socket
import time

CSS = """
body { margin:0; background:#fff; font-family:sans-serif; }
.blk { padding:8px 20px; }
h3 { margin:6px 0; font-size:26px; }
/* V1 纯块级基线 */
.v1 .bar { display:block; height:30px; border:3px solid #000; margin:6px 0; }
/* V2 table */
.v2 .tr { display:table-row; }
.v2 .td { display:table-cell; vertical-align:middle; font-size:28px; padding:0 10px; }
.v2 .bar { display:table-cell; height:30px; border:3px solid #000; }
/* V3 -webkit-box */
.v3 .r { display:-webkit-box; -webkit-box-orient:horizontal; }
.v3 .l { width:120px; font-size:28px; }
.v3 .bar { -webkit-box-flex:1; height:30px; border:3px solid #000; margin:0 12px; }
.v3 .v { width:140px; font-size:28px; text-align:right; }
/* V4 unprefixed flex */
.v4 .r { display:flex; align-items:center; }
.v4 .l { width:120px; font-size:28px; }
.v4 .bar { flex:1; height:30px; border:3px solid #000; margin:0 12px; }
.v4 .v { width:140px; font-size:28px; text-align:right; }
/* V5 inline-block */
.v5 .l { display:inline-block; width:120px; font-size:28px; vertical-align:middle; }
.v5 .bar { display:inline-block; width:60%; height:30px; border:3px solid #000; vertical-align:middle; }
.v5 .v { display:inline-block; width:140px; font-size:28px; text-align:right; vertical-align:middle; }
/* V6 float */
.v6 { overflow:hidden; padding:8px 0; }
.v6 .l { float:left; width:120px; font-size:28px; }
.v6 .bar { overflow:hidden; height:30px; border:3px solid #000; margin:0 12px; }
.v6 .v { float:right; width:140px; font-size:28px; text-align:right; }
"""

BODY = "".join(
    '<div class="blk v%d"><h3>V%d %s</h3>%s</div>' % (
        i, i, name,
        ''.join('<div class="tr"><div class="td l">L%d</div><div class="bar"></div>'
                '<div class="td v">V%d</div></div>' % (i, i) if i == 2 else
                '<div class="r"><div class="l">L%d</div><div class="bar"></div>'
                '<div class="v">V%d</div></div>' % (i, i) if i in (3, 4) else
                '<div><div class="l">L%d</div><div class="bar"></div>'
                '<div class="v">V%d</div></div>' % (i, i)
                for i in (1, 2, 3)  # 每方案 3 行占位，足够目测
            )
    )
    for i, name in [(1, 'block'), (2, 'table'), (3, 'webkit-box'),
                    (4, 'flex'), (6, 'float')]
)


def build_html():
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>LAYTEST</title>'
            '<style>%s</style></head><body>%s</body></html>' % (CSS, BODY))


def telnet(cmds, waits):
    s = socket.create_connection(('192.168.15.244', 23), timeout=8)
    s.settimeout(2)
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


html = build_html()
print('TESTPAGE_BYTES', len(html))
# 经 base64 分块推到设备（约 2.2KB → b64 ~3KB，单条 telnet 行内推送）
import base64
b64 = base64.b64encode(html.encode('utf-8')).decode('ascii')
o = telnet([
    'echo %s > /var/local/mesquite/com.kindle.macpanel/laytest.b64' % b64,
    'base64 -d /var/local/mesquite/com.kindle.macpanel/laytest.b64 > /var/local/mesquite/com.kindle.macpanel/index.html; wc -c /var/local/mesquite/com.kindle.macpanel/index.html; echo PUSHED',
    'killall mesquite 2>/dev/null; sleep 2; lipc-set-prop com.lab126.appmgrd start app://com.kindle.macpanel; sleep 12; eips -s w=1088,h=1536 -f; gzip -c /dev/fb0 > /tmp/fb.gz && curl -s --noproxy "*" -T /tmp/fb.gz "http://192.168.15.201:18866/fb0" -w " HTTP:%{http_code}"; echo SNAP',
], [2, 3, 26])
print(o[1].strip().splitlines()[-3:])
print(o[2].strip().splitlines()[-4:])
