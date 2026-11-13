#!/usr/bin/env python3
# layout A/B test page for mesquite
import base64
import socket
import time

HOST = ('192.168.15.244', 23)
APP = '/var/local/mesquite/com.kindle.macpanel'

CSS = """
body { margin:0; background:#fff; font-family:sans-serif; }
.blk { padding:8px 20px; }
h3 { margin:6px 0; font-size:26px; }
.v1 .bar { display:block; height:30px; border:3px solid #000; margin:6px 0; }
.v2 .tr { display:table-row; }
.v2 .td { display:table-cell; vertical-align:middle; font-size:28px; padding:0 10px; }
.v2 .bar { display:table-cell; height:30px; border:3px solid 0 0 0; }
"""
