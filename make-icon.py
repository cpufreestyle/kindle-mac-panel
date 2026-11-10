#!/usr/bin/env python3
"""画告警 App 图标：深色仪表盘 + 折线 + 警示，512/1024 两尺寸"""
from PIL import Image, ImageDraw, ImageFont
import os

def draw(size):
    S = size / 512.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    # macOS squircle 近似圆角底
    r = int(110 * S)
    dr.rounded_rectangle([int(20*S), int(20*S), size - int(20*S), size - int(20*S)],
                         radius=r, fill=(28, 30, 34, 255))
    # 顶部标题条：MAC 告警
    try:
        fp = "/System/Library/Fonts/STHeiti Medium.ttc"
        f1 = ImageFont.truetype(fp, int(64 * S))
        f2 = ImageFont.truetype(fp, int(40 * S))
    except Exception:
        f1 = f2 = None
    dr.text((int(50*S), int(52*S)), "MAC", font=f1, fill=(240, 240, 240, 255))
    dr.text((int(50*S + 168*S), int(64*S)), "告警", font=f2, fill=(255, 159, 10, 255))
    # 折线（趋势）
    pts = [(60, 400), (140, 340), (210, 360), (280, 280), (350, 300), (430, 210), (452, 205)]
    pts = [(int(x*S), int(y*S)) for x, y in pts]
    dr.line(pts, fill=(255, 255, 255, 255), width=int(14*S), joint="curve")
    for p in pts[::2]:
        dr.ellipse([p[0]-int(9*S), p[1]-int(9*S), p[0]+int(9*S), p[1]+int(9*S)],
                   fill=(255, 255, 255, 255))
    # 警示三角（右下）
    tx, ty, ts = int(330*S), int(120*S), int(140*S)
    dr.polygon([(tx+ts//2, ty), (tx+ts, ty+int(ts*0.88)), (tx, ty+int(ts*0.88))],
               fill=(255, 159, 10, 255))
    dr.text((tx+int(ts//2-14*S), ty+int(28*S)), "!", font=f1, fill=(28, 30, 34, 255))
    # 底部状态条
    dr.rounded_rectangle([int(60*S), int(440*S), int(452*S), int(462*S)],
                         radius=int(10*S), fill=(255, 159, 10, 255))
    return img

os.makedirs("/tmp/alert-icon.iconset", exist_ok=True)
draw(1024).save("/tmp/alert-icon_512@2x.png")
draw(512).save("/tmp/alert-icon_512.png")
draw(256).save("/tmp/alert-icon_256.png")
draw(128).save("/tmp/alert-icon_128.png")
draw(64).save("/tmp/alert-icon_32@2x.png")
draw(32).save("/tmp/alert-icon_32.png")
print("OK 512 预览已保存")
