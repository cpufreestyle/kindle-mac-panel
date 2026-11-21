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

# 1. 找 Kindle 的 USB 网卡。
#    两条路径：新出现的接口（插线后跑本脚本）；已经在了（先插线后跑本脚本，
#    这次就踩到过 —— 只比对新接口会误报「没出现」）。
#    判定：接口有 192.168.15.201 → 就是它；否则找没有 IPv4 的 en* 逐个试配。
BEFORE=$(ifconfig -l | tr ' ' '\n' | grep -E '^(en|usb)' | sort | tr '\n' ' ')
echo "现有接口: $BEFORE"

FOUND=""
# 路径 A：已经配好了
for x in $(ifconfig -l | tr ' ' '\n' | grep -E '^(en|usb)'); do
    if ifconfig "$x" 2>/dev/null | grep -q "inet 192\.168\.15\.201 "; then
        FOUND="$x"; echo "✓ 已找到配好的接口: $x (192.168.15.201)"; break
    fi
done

# 路径 B：等新接口，或在现有接口里找没 IP 的
if [ -z "$FOUND" ]; then
    echo "等待 Kindle USB 网卡（最多 ${WAIT}s）..."
    i=0
    while [ $i -lt "$WAIT" ]; do
        AFTER=$(ifconfig -l | tr ' ' '\n' | grep -E '^(en|usb)' | sort | tr '\n' ' ')
        # B1：新出现的接口
        for x in $AFTER; do
            if ! echo " $BEFORE " | grep -q " $x "; then
                FOUND="$x"; break
            fi
        done
        # B2：现有接口里没配 IPv4 的（en0 有 WiFi IP 会跳过）
        if [ -z "$FOUND" ]; then
            for x in $AFTER; do
                if ! ifconfig "$x" 2>/dev/null | grep -q "inet "; then
                    FOUND="$x"; break
                fi
            done
        fi
        if [ -n "$FOUND" ]; then break; fi
        i=$((i + 2))
        sleep 2
    done
fi

if [ -z "$FOUND" ]; then
    echo "✗ ${WAIT}s 内没找到可用的 USB 网卡"
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

# 2. 给它配静态 IP
if ! ifconfig "$FOUND" 2>/dev/null | grep -q "inet 192\.168\.15\.201 "; then
    if ifconfig "$FOUND" 192.168.15.201 netmask $MASK up 2>/dev/null; then
        echo "✓ $FOUND 已配 $TARGET_IP/$MASK"
    else
        echo "✗ 给 $FOUND 配 $TARGET_IP 失败"
        exit 1
    fi
else
    echo "✓ $FOUND 已经是 $TARGET_IP"
fi
CFG="$FOUND"

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
