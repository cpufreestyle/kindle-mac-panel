#!/usr/bin/env pwsh
<#
.SYNOPSIS
    win-metrics 一键安装器（Windows 侧）—— 装计划任务 + 自检 + 启动。

.DESCRIPTION
    在 NUC9 这类 Windows 机器上跑一次即可完成：
      1. 检查 PowerShell 7 是否可用
      2. 把 win-metrics.ps1 复制到 C:\win-metrics.ps1（短路径，避免计划任务 quoting 问题）
      3. 注册开机自启计划任务 win-metrics（SYSTEM 账号，AtStartup）
      4. 启动任务并做一次连通性自检

    必须用「管理员」PowerShell 运行（Register-ScheduledTask 提权）。

.EXAMPLE
    git clone https://github.com/cpufreestyle/kindle-mac-panel.git C:\kp
    powershell -ExecutionPolicy Bypass -File C:\kp\install-win-metrics.ps1
#>
[CmdletBinding()]
param(
    # 任务名
    [string]$TaskName = 'win-metrics',
    # 采集脚本安装到哪（短路径最稳）
    [string]$InstallDir = 'C:\',
    # 采集间隔（秒）
    [int]$IntervalSec = 5,
    # 显式指定 Mac IP（留空 = 自动发现）
    [string]$MacIp = ''
)

$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'win-metrics.ps1'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

Say "==== win-metrics 安装器 ====" 'Cyan'

# ── 0. 平台检查 ─────────────────────────────────────────────────────────────
if (-not $IsWindows) {
    Say "✗ 这个安装器只能在 Windows 上跑（当前: $($PSVersionTable.OS)）" 'Red'
    exit 1
}

# ── 1. 检查 PowerShell 7 ─────────────────────────────────────────────────────
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwsh) {
    Say "✗ 找不到 pwsh（PowerShell 7）" 'Red'
    Say "  本脚本按 PowerShell 7 写的（用户偏好 PS7）。请先安装：" 'Yellow'
    Say "  winget install --id Microsoft.PowerShell --source winget" 'Yellow'
    Say "  或从 https://aka.ms/powershell 安装后重试。" 'Yellow'
    exit 1
}
$pwshPath = $pwsh.Source
Say "✓ PowerShell 7: $pwshPath ($(& $pwshPath -NoProfile -c '$PSVersionTable.PSVersion.ToString()'))"

# ── 2. 复制采集脚本 ──────────────────────────────────────────────────────────
if (-not (Test-Path $src)) {
    Say "✗ 找不到 $src（应与本安装器同目录）" 'Red'
    exit 1
}
$dst = $null
try {
    $dst = Join-Path $InstallDir 'win-metrics.ps1'
    Copy-Item -Path $src -Destination $dst -Force
    Say "✓ 采集脚本已安装: $dst"
} catch {
    Say "✗ 复制失败: $($_.Exception.Message)" 'Red'
    Say "  换个短路径试试，比如 -InstallDir C:\kp" 'Yellow'
    exit 1
}

# ── 3. 注册计划任务 ──────────────────────────────────────────────────────────
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dst`""
if ($MacIp) { $arg += " -MacIp $MacIp" }
$arg += " -IntervalSec $IntervalSec"

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $pwshPath -Argument $arg
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
    # ExecutionTimeLimit=0 → 允许长跑（脚本是常驻循环）
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description '推送 Windows 指标到 Mac 运维值班台' -Force | Out-Null
    Say "✓ 计划任务已注册: $TaskName（SYSTEM 账号，开机自启）"
} catch {
    Say "✗ 注册计划任务失败: $($_.Exception.Message)" 'Red'
    Say "  请确认是用「管理员」PowerShell 运行的。" 'Yellow'
    exit 1
}

# ── 4. 启动任务 ──────────────────────────────────────────────────────────────
try {
    Start-ScheduledTask -TaskName $TaskName
    Say "✓ 任务已启动"
} catch {
    Say "✗ 启动任务失败: $($_.Exception.Message)" 'Red'
}

Start-Sleep -Seconds 8

# ── 5. 自检：跑一次 -Check 看链路 ────────────────────────────────────────────
Say ""
Say "---- 自检（一次采集 + 推送）----" 'Cyan'
& $pwshPath -NoProfile -ExecutionPolicy Bypass -File $dst -Check
$checkCode = $LASTEXITCODE

Say ""
if ($checkCode -eq 0) {
    Say "==== 安装完成 ✓ ====" 'Green'
    Say "面板：http://<Mac-IP>:18866/panel.html （WIN 卡出现即数据已通）"
    Say "Mac 侧推送日志：/tmp/kindle-win.log"
    Say "本机日志：Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
} else {
    Say "==== 安装完成，但自检没通过（退出码 $checkCode）====" 'Yellow'
    Say "按上面的自检输出排查；常见原因："
    Say "  · 找不到 Mac —— 用 -MacIp <Mac-IP> 显式指定"
    Say "  · Mac 上 server.py 没跑 —— 在 Mac 上查 launchctl list | com.kindle.panel"
    Say "  · 防火墙拦出站 —— New-NetFirewallRule -DisplayName 'win-metrics out' -Direction Outbound -RemoteAddress <Mac-IP> -RemotePort 18866 -Protocol TCP -Action Allow"
}
exit $checkCode
