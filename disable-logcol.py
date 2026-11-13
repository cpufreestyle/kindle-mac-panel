#!/usr/bin/env python3
# ⛔️ 此脚本已废弃，禁止运行！
#
# 2026-11-14 实测事故：本脚本把 /etc/upstart/ 下的 demd.conf、clickstream_logging.conf、
# fastmetrics.conf 改名为 .disabled，导致 Kindle 框架启动链（upstart 依赖
# `start on started demd` 等事件）永远等不到事件，设备卡在开机转圈（powerd/appmgrd
# 全部不启动），被迫恢复文件 + 重启 4 次 + 用户强制重启 1 次才救回。
#
# 弹窗的正确治理方式（均已落地，不需要禁用这些服务）：
#   1. core_pattern 改为有效管道丢弃（watchdog v3.2）；
#   2. 面板 HTML 不含 JS（时钟 JS 已回退）；
#   3. float 布局稳定后 mesquite 不再崩溃，无崩溃即无弹窗。
#
# 如历史遗留 .disabled 文件需要恢复：
#   ssh/telnet 到设备后执行：
#     mount -o remount,rw /
#     mv /etc/upstart/demd.conf.disabled /etc/upstart/demd.conf
#     mv /etc/upstart/clickstream_logging.conf.disabled /etc/upstart/clickstream_logging.conf
#     mv /etc/upstart/fastmetrics.conf.disabled /etc/upstart/fastmetrics.conf
#     mount -o remount,ro / ; sync ; reboot
#   （last_debug_info.conf.disabled 可以留着，实测不挡启动）
raise SystemExit('⛔️ 禁止运行：此脚本会导致 Kindle 无法开机（见文件头注释）。如需恢复 .disabled 文件请看注释。')
