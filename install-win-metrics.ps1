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
# 用 $env:OS 判断而不是 $IsWindows —— 后者是 PowerShell 6+ 独有的变量，
# 在 Windows PowerShell 5.1 上是 $null，会误判成「非 Windows」直接退出。
if ($env:OS -ne 'Windows_NT') {
    Say "✗ 这个安装器只能在 Windows 上跑（当前: $($env:OS)）" 'Red'
    exit 1
}

# ── 1. 检查 PowerShell 7 ─────────────────────────────────────────────────────
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
# PS7 有就用 PS7（用户偏好）；没有就退回当前 PowerShell —— 两个脚本都没用
# PS7-only 语法，Windows PowerShell 5.1 也能跑，少一道坎。
if ($pwsh) {
    $pwshPath = $pwsh.Source
    $ver = & $pwshPath -NoProfile -c '$PSVersionTable.PSVersion.ToString()'
    Say "✓ PowerShell 7: $pwshPath ($ver)"
} else {
    $pwshPath = (Get-Process -Id $PID).Path
    Say "! 没找到 pwsh（PowerShell 7），改用当前 PowerShell $($PSVersionTable.PSVersion)" 'Yellow'
    Say "  建议装 PS7：winget install --id Microsoft.PowerShell --source winget" 'Yellow'
}

# ── 2. 准备采集脚本 ──────────────────────────────────────────────────────────
# 同目录没有就下载。优先从 Mac 拿（局域网可达、不依赖 GitHub —— 仓库是 private，
# NUC9 上没凭据 clone 不了），Mac 拿不到再试 GitHub raw。
if (-not (Test-Path $src)) {
    $tried = @()
    # 候选 Mac 地址：显式参数 → 缓存 → 常见网段
    $cands = @()
    if ($MacIp) { $cands += $MacIp }
    try {
        $cf = "$env:LOCALAPPDATA\win-metrics\mac-ip.txt"
        if (Test-Path $cf) {
            $cached = (Get-Content $cf -ErrorAction Stop | Select-Object -First 1)
            if ($cached) { $cands += $cached.Trim() }
        }
    } catch { }
    $cands += @('192.168.1.112', '192.168.15.201')

    foreach ($c in ($cands | Select-Object -Unique)) {
        if (-not $c) { continue }
        $tried += $c
        # 先探活（/lan.txt 返回 Mac 的局域网 IP），确认是 Mac 再下
        try {
            $probe = Invoke-WebRequest -Uri "http://${c}:${Port}/lan.txt" `
                -UseBasicParsing -TimeoutSec 3
            if ($probe.StatusCode -ne 200 -or -not $probe.Content) { continue }
        } catch { continue }
        try {
            Invoke-WebRequest -Uri "http://${c}:${Port}/win/win-metrics.ps1" `
                -OutFile $src -UseBasicParsing -TimeoutSec 8
            Say "✓ 已从 Mac 下载采集脚本: $c (Mac IP $($probe.Content.Trim()))" 'Green'
            $MacIp = $probe.Content.Trim()   # 顺手把真实 IP 记下来，后面传给采集脚本
            break
        } catch { }
    }
    if (-not (Test-Path $src)) {
        Say "从 Mac 下载失败（试过: $($tried -join ', ')），改试 GitHub raw…" 'Yellow'
        try {
            Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/cpufreestyle/kindle-mac-panel/main/win-metrics.ps1' `
                -OutFile $src -UseBasicParsing -TimeoutSec 30
            Say "✓ 已下载: $src"
        } catch {
            Say "✗ 下载失败: $($_.Exception.Message)" 'Red'
            Say "  把 win-metrics.ps1 手动放到本脚本同目录即可" 'Yellow'
            exit 1
        }
    }
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
