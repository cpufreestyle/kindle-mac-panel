#!/bin/bash
# 构建 "Mac告警.app"（自定义图标 + 动态消息）并自测
set -e
APP="/Applications/Mac告警.app"
S=$(cd "$(dirname "$0")" && pwd)

# 1. iconset -> icns
rm -rf /tmp/alert-icon.iconset
mkdir -p /tmp/alert-icon.iconset
cp /tmp/alert-icon_512@2x.png /tmp/alert-icon.iconset/icon_512x512@2x.png
cp /tmp/alert-icon_512.png   /tmp/alert-icon.iconset/icon_128x128@2x.png
cp /tmp/alert-icon_256.png   /tmp/alert-icon.iconset/icon_256x256.png
cp /tmp/alert-icon_128.png   /tmp/alert-icon.iconset/icon_128x128.png
cp /tmp/alert-icon_32@2x.png /tmp/alert-icon.iconset/icon_32x32@2x.png
cp /tmp/alert-icon_32.png    /tmp/alert-icon.iconset/icon_32x32.png
iconutil -c icns /tmp/alert-icon.iconset -o /tmp/alert-icon.icns

# 2. AppleScript app 壳（读消息文件发通知）
cat > /tmp/alert-src.applescript <<'EOF'
on run
    set msg to ""
    try
        set msg to do shell script "cat /tmp/kindle-alert-msg"
    end try
    if msg is "" then
        display notification "系统状态正常" with title "Mac 告警"
    else
        display notification msg with title "Mac 告警" sound name "Ping"
    end if
end run
EOF
osacompile -o "$APP" /tmp/alert-src.applescript

# 3. 换图标
cp /tmp/alert-icon.icns "$APP/Contents/Resources/applet.icns"
plutil -replace CFBundleName -string "Mac告警" "$APP/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "Mac告警" "$APP/Contents/Info.plist"
touch "$APP"

# 4. 刷新 LaunchServices 图标缓存
killall Finder 2>/dev/null || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

# 5. 自测通知
echo "图标已上线 - GPU 高载 73%（测试）" > /tmp/kindle-alert-msg
open -g "$APP"
echo "DONE: $APP"
