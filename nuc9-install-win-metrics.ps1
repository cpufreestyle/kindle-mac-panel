<#
.SYNOPSIS
    NUC9 (Windows) 一键接入 Mac 运维值班台（kindle-mac-panel）。

.DESCRIPTION
    在 NUC9 的「管理员」PowerShell 中运行此脚本，即可把 NUC9 的
    CPU/内存/磁盘/网速指标推送到 Mac 上的 kindle-mac-panel 面板。

    等价于运行：
      iwr http://192.168.1.112:18866/win/install-win-metrics.ps1 -OutFile C:\iwm.ps1
      powershell -ExecutionPolicy Bypass -File C:\iwm.ps1

    前提：Mac 上 server.py 已在跑，且 NUC9 能访问 Mac 的 18866 端口（已实测可达）。

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File C:\path\to\nuc9-install-win-metrics.ps1
#>

$ErrorActionPreference = 'Stop'

$MacIp   = '192.168.1.112'
$MacPort = 18866
$InstallerUrl = "http://${MacIp}:${MacPort}/win/install-win-metrics.ps1"
$LocalInstaller = 'C:\iwm.ps1'

Write-Host "==== NUC9 win-metrics 一键安装 ====" -ForegroundColor Cyan
Write-Host "Mac 面板地址: http://${MacIp}:${MacPort}/panel.html" -ForegroundColor Gray
Write-Host ""

# 1. 从 Mac 下载安装器（NUC9 无 GitHub 凭据，不走 git clone；仓库是 private）
Write-Host "[1/3] 从 Mac 下载安装器 ..." -ForegroundColor Gray
try {
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $LocalInstaller -UseBasicParsing -TimeoutSec 30
    Write-Host "    ✓ 已下载到 $LocalInstaller" -ForegroundColor Green
} catch {
    Write-Host "    ✗ 下载失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    请确认 Mac 上 server.py 在运行，且 NUC9 能访问 http://${MacIp}:${MacPort}/lan.txt" -ForegroundColor Yellow
    exit 1
}

# 2. 运行安装器（检查 PS7、下载采集脚本、注册开机自启计划任务、启动并自检）
Write-Host "[2/3] 运行安装器 ..." -ForegroundColor Gray
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$LocalInstaller`""
& pwsh @($arg -split ' ', 2)
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "[3/3] 安装完成 ✓" -ForegroundColor Green
    Write-Host "    验证：在浏览器打开 http://${MacIp}:${MacPort}/panel.html，面板应出现 `WIN · NUC9 · 在线`" -ForegroundColor Gray
    Write-Host "    Mac 推送日志: /tmp/kindle-win.log（应有来自 .68 的记录）" -ForegroundColor Gray
    Write-Host "    本机任务信息: Get-ScheduledTask -TaskName win-metrics | Get-ScheduledTaskInfo" -ForegroundColor Gray
} else {
    Write-Host "[3/3] 安装完成，但自检未通过（退出码 $code）" -ForegroundColor Yellow
    Write-Host "    常见原因：找不到 Mac / Mac 上 server.py 未跑 / 防火墙拦 18866 出站" -ForegroundColor Yellow
    Write-Host "    排查: pwsh -NoProfile -File $LocalInstaller -Check" -ForegroundColor Gray
}
exit $code
