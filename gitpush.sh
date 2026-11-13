#!/bin/bash
# git 自动推送 — 代理恢复后自动完成（成功后自卸载 launchd 任务）
cd /Users/a1-6/kindle-panel
if git push > /tmp/gitpush.log 2>&1; then
  /bin/launchctl bootout "gui/$(id -u)" /Users/a1-6/Library/LaunchAgents/com.kindle.gitpush.plist 2>/dev/null
  rm -f /Users/a1-6/Library/LaunchAgents/com.kindle.gitpush.plist
  rm -f "$0"
  echo "PUSH_OK $(date '+%m-%d %H:%M')"
else
  echo "PUSH_RETRY $(date '+%H:%M')"
fi
