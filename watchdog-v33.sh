#!/bin/sh
# USBNet watchdog v3.3 — cron 每分钟调用
# v3.2 基础上新增：面板数据链路保活（fetchloop 拉起）+ demd 保活死亡（弹窗宿主运行时停用）
# ENABLED 标志存在时：确保 telnetd 存活 + gadget 在位，否则按 v3 逻辑重建
FLAG=/mnt/us/usbnet/ENABLED
[ -f "$FLAG" ] || exit 0

# --- 防垃圾再生（core_pattern 用有效管道丢弃，弹窗不再触发）---
echo '|/bin/true' > /proc/sys/kernel/core_pattern 2>/dev/null
rm -f /mnt/us/cvm_*.core /mnt/us/mesquite_*.core /mnt/us/Indexer_Dump_*.txt 2>/dev/null

# --- 面板数据链路保活：主 fetchloop 不在则拉起（2026-11-14 实测 boot 后会丢）---
pgrep -f usbnet/fetchloop.sh >/dev/null 2>&1 || (nohup /mnt/us/usbnet/fetchloop.sh >/dev/null 2>&1 &)

# --- demd 保活死亡：弹窗宿主服务，运行时停用（boot 依赖已实测无碍，禁改名 upstart conf）---
initctl stop demd >/dev/null 2>&1

NEED_REBUILD=0
netstat -ltn 2>/dev/null | grep -q ':23 ' || NEED_REBUILD=1
lsmod | grep -q g_ether || NEED_REBUILD=1
[ -e /sys/class/net/usb0 ] || NEED_REBUILD=1

[ "$NEED_REBUILD" = "1" ] || exit 0

{
date
echo "watchdog: 通道死亡，v3 自愈重建"
ifconfig usb0 down 2>/dev/null
killall telnetd 2>/dev/null
mount | grep -q configfs || mount -t configfs none /sys/kernel/config 2>/dev/null
for g in /sys/kernel/config/usb_gadget/*; do
    [ -e "$g" ] || continue
    [ -e "$g/UDC" ] && echo "" > "$g/UDC" 2>/dev/null
    rm -rf "$g"
done
rmmod g_ether 2>/dev/null
rmmod usb_f_rndis 2>/dev/null
rmmod usb_f_ecm 2>/dev/null
rmmod u_ether 2>/dev/null
rmmod g_mass_storage 2>/dev/null
rmmod usb_f_mass_storage 2>/dev/null
sleep 1
ATTEMPT=0
while [ $ATTEMPT -lt 3 ]; do
    ATTEMPT=$((ATTEMPT+1))
    modprobe g_ether 2>&1
    sleep 2
    [ -e /sys/class/net/usb0 ] && break
    for g in /sys/kernel/config/usb_gadget/*; do
        [ -e "$g" ] || continue
        [ -e "$g/UDC" ] && echo "" > "$g/UDC" 2>/dev/null
        rm -rf "$g"
    done
    rmmod g_mass_storage 2>/dev/null
    rmmod usb_f_mass_storage 2>/dev/null
done
if [ ! -e /sys/class/net/usb0 ]; then
    echo "watchdog v3.3: 3次尝试失败，兜底恢复U盘模式"
    modprobe g_mass_storage 2>&1
else
    ifconfig usb0 192.168.15.244 netmask 255.255.255.0 up 2>&1
    busybox telnetd -l /bin/sh -p 23 2>&1
    echo "watchdog v3.3: 重建完成"
fi
} >> /mnt/us/usbnet/watchdog.log 2>&1
