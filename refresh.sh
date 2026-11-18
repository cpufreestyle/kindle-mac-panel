#!/bin/sh
# Mac 面板数据刷新 — cron 每分钟调用（20 秒错峰 + 原子替换）
D=/var/local/mesquite/com.kindle.macpanel
sleep 20
curl -s --max-time 8 http://192.168.15.201:18866/panel.html -o $D/index.new
mv -f $D/index.new $D/index.html
