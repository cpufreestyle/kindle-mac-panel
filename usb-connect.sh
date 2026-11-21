#!/bin/sh
# usb-connect.sh — Kindle USB 网络一键接驳
#
# 背景：Kindle 插上 Mac 的 USB 后，macOS 会枚举出一个 USB 网卡（enX）。
# 但这个网卡的 IP **没有任何脚本自动配置** —— Kindle 侧靠 192.168.15.201
# 找 Mac（fetchloop 的 PROBES 第一位就是它），所以每次插线都得手动配。
# 本脚本：等新网卡出现 → 配 192.168.15.201/24 → 等 telnet(23) 通 → 报结果。
#
# 用法：sh usb-connect.sh [等待秒数，默认 90]
#
# 注意：Kindle 侧必须已跑起 USBNet（watchdog 会自动切 RNDIS）；
# 若 Kindle 停在 USB mass-storage（U 盘）模式，先 diskutil eject /Volumes/Kindle，
# watchdog 下一分钟会自动切回 RNDIS（实测 eject 后 ~75s telnet 恢复）。

WAIT=${1:-90}
TARGET_IP=192.168.15.201
KINDLE_IP=192.168.15.244   # Kindle 侧 usb0
MASK=255.255.255.0

echo "== Kindle USB 接驳 =="

# 1. 记录当前接口集合，等新网卡冒头
BEFORE=$(ifconfig -l | tr ' ' '\n' | grep -E '^(en|usb)' | sort | tr '\n' ' ')
echo "现有接口: $BEFORE"

echo "等待新 USB 网卡（最多 ${WAIT}s）..."
NEW=""
i=0
while [ $i -lt "$WAIT" ]; do
    AFTER=$(ifconfig -l | tr ' ' '\n' | grep -E '^(en|usb)' | sort | tr '\n' ' ')
    NEW=""
    for x in $AFTER; do
        # 出现在 BEFORE 里的是老接口（en0 WiFi / bridge0 / awdl 等），跳过
        if ! echo " $BEFORE " | grep -q " $x "; then
            NEW="$NEW $x"
        fi
    done
    if [ -n "$NEW" ]; then break; fi
    i=$((i + 2))
    sleep 2
done

NEW=$(echo $NEW)   # 去掉首尾空格

if [ -z "$NEW" ]; then
    echo "✗ ${WAIT}s 内没有新的 USB 网卡出现"
    echo ""
    echo "  macOS 侧排查："
    echo "    ioreg -p IOUSB -l -w 0 | grep -i 'USB Product Name'   # 看有没有 Amazon/Kindle"
    echo "    diskutil list | grep -i kindle                        # 看是不是被当成 U 盘"
    echo "    system_profiler SPUSBDataType | grep -i amazon"
    echo ""
    echo "  多半是物理层问题，按顺序试："
    echo "    1. **直插 Mac，别走 USB HUB**（Kindle 供电吃紧，HUB 上枚举失败最常见）"
    echo "    2. 换一根**数据线**（纯充电线没有数据线芯）"
    echo "    3. 按电源键唤醒 Kindle（息屏时 USB gadget 可能不供电）"
    echo "    4. 换个 USB 口（尤其别用前面板无源 HUB）"
    echo "    5. 若是 U 盘模式：diskutil eject /Volumes/Kindle，等 ~75s 再试"
    exit 1
fi

echo "✓ 发现新接口: $NEW"

# 2. 给它配静态 IP
CFG=""
for x in $NEW; do
    if ifconfig "$x" 192.168.15.201 netmask $MASK up 2>/dev/null; then
        CFG="$x"; break
    fi
done

if [ -z "$CFG" ]; then
    echo "✗ 给 $NEW 配 $TARGET_IP 失败"
    exit 1
fi
echo "✓ $CFG 已配 $TARGET_IP/$MASK"

# 3. 等 Kindle 侧 telnet 起来
echo "等 Kindle ($KINDLE_IP) telnet:23 …"
j=0
while [ $j -lt 60 ]; do
    if nc -z -G 2 -w 2 "$KINDLE_IP" 23 2>/dev/null; then
        echo "✓ telnet:23 已通"
        echo ""
        echo "接下来：Kindle 侧 fetchloop 会在 15s 内自动拉最新面板。"
        echo "想立刻刷新可以在 Kindle 上跑 refresh-now，或等它自己来。"
        exit 0
    fi
    j=$((j + 3))
    sleep 3
done

echo "✗ 120s 内 telnet:23 没通"
echo ""
echo "  说明 Kindle 侧 USBNet 还没起来。设备端排查（telnet 上去后）："
echo "    pgrep -f watchdog.sh          # watchdog 在跑吗"
echo "    ip addr show usb0             # usb0 在吗"
echo "    tail -50 /tmp/usbnet.log      # USBNet 切换日志"
echo "  如果 Kindle 是 U 盘模式：Mac 上 diskutil eject /Volumes/Kindle，watchdog 会自动切 RNDIS。"
exit 1
