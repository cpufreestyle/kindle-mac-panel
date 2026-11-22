#!/usr/bin/env pwsh
<#
.SYNOPSIS
    在非 Windows 机器上演练 install-win-metrics.ps1 的完整流程（回归测试）。

.DESCRIPTION
    Windows 侧的安装器没法在 Mac 上真机跑，但它的逻辑（下载 → 复制 → 注册任务
    → 自检）可以在本机模拟。做法：桩掉 ScheduledTasks 模块的 7 个 cmdlet，
    绕过平台检查，然后跑真安装器并断言关键结果。

    这个演练抓到过 3 个真 bug（都不是"看起来像对"的问题）：
      1. 安装器 param 里没有 -Port，下载逻辑引用未定义的 $Port
         → URL 变成 http://192.168.1.112:/lan.txt 直接失败
      2. 采集脚本已下到 C:\win-metrics.ps1 时，Copy-Item 到同一路径报
         "Cannot overwrite the item ... with itself"（一行命令安装方式必然触发）
      3. 平台判断用 $IsWindows（PS6+ 独有变量），5.1 上是 $null → 误判非 Windows

.EXAMPLE
    pwsh -NoProfile -File test-install-sim.ps1
    pwsh -NoProfile -File test-install-sim.ps1 -MacIp 192.168.1.112
#>
[CmdletBinding()]
param(
    # 模拟安装目录
    [string]$InstallDir = '',
    # 要连的 Mac（面板服务要开着）
    [string]$MacIp = '',
    [int]$Port = 18866
)

$ErrorActionPreference = 'Stop'
$BASE = $PSScriptRoot
if (-not $InstallDir) {
    $InstallDir = Join-Path ([System.IO.Path]::GetTempPath()) ("win-install-sim-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$fails = @()
function Assert($cond, $msg) {
    if ($cond) { Write-Host "  ✓ $msg" }
    else { Write-Host "  ✗ $msg" -ForegroundColor Red; $script:fails += $msg }
}

# ── 1. 桩掉 ScheduledTasks 的 cmdlet ─────────────────────────────────────────
$stub = @'
function New-ScheduledTaskAction { param($Execute, $Argument)
    [pscustomobject]@{ Execute = $Execute; Argument = $Argument } }
function New-ScheduledTaskTrigger { param([switch]$AtStartup)
    [pscustomobject]@{ AtStartup = $AtStartup } }
function New-ScheduledTaskPrincipal { param($UserId, $RunLevel)
    [pscustomobject]@{ UserId = $UserId; RunLevel = $RunLevel } }
function New-ScheduledTaskSettingsSet { param($ExecutionTimeLimit,
    [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries,
    [switch]$StartWhenAvailable)
    [pscustomobject]@{ ExecutionTimeLimit = $ExecutionTimeLimit } }
function Register-ScheduledTask { param($TaskName, $Action, $Trigger, $Principal,
    $Settings, $Description, [switch]$Force)
    $global:REGISTERED = [pscustomobject]@{ TaskName = $TaskName; Action = $Action;
        Trigger = $Trigger; Principal = $Principal; Settings = $Settings }
    [pscustomobject]@{ TaskName = $TaskName } }
function Unregister-ScheduledTask { param($TaskName, $Confirm) }
function Start-ScheduledTask { param($TaskName) }
function Get-ScheduledTaskInfo { param($TaskName) }
'@
$stubPath = Join-Path $InstallDir 'sim-cmdlets.ps1'
Set-Content -Path $stubPath -Value $stub -Encoding UTF8
. $stubPath
$global:REGISTERED = $null

# ── 2. 绕过平台检查（$env:OS 在 Windows 上才是 Windows_NT）────────────────
$env:OS = 'Windows_NT'
$env:LOCALAPPDATA = Join-Path $InstallDir 'localapp'
New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null

# ── 3. 跑真安装器 ──────────────────────────────────────────────────────────
$installer = Join-Path $BASE 'install-win-metrics.ps1'
if (-not (Test-Path $installer)) { Write-Host "✗ 找不到 $installer" -ForegroundColor Red; exit 1 }

# 安装器的 Say() 用 Write-Host —— 不进管道，捕获不到。临时覆盖 Write-Host
# 把文本收集起来（覆盖期间只收集不打印，跑完立刻恢复 cmdlet）。
$global:SAID = @()
function Write-Host {
    param([Parameter(ValueFromPipeline = $true, Position = 0)]$Object,
          [string]$ForegroundColor)
    $global:SAID += "$Object"
}

# 注意：不要用 $args —— 那是 PowerShell 的保留自动变量（脚本参数），
# 拿它做参数数组会被当成位置参数传给安装器。
$installArgs = @{ InstallDir = $InstallDir; Port = $Port }
if ($MacIp) { $installArgs['MacIp'] = $MacIp }
try {
    $out = & $installer @installArgs 2>&1 | Out-String
    $code = $LASTEXITCODE
} finally {
    Remove-Item -Path Function:\Write-Host -ErrorAction SilentlyContinue
}
$allText = $global:SAID -join "`n"

Write-Host "== 演练 install-win-metrics.ps1 ==" -ForegroundColor Cyan
Write-Host "  安装目录: $InstallDir"
Write-Host "  Mac      : $(if ($MacIp) { $MacIp } else { '(自动发现)' })"
Write-Host ""
Write-Host "-- 安装器输出 --"
$global:SAID | ForEach-Object { Write-Host "  $_" }

# ── 4. 断言 ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "-- 断言 --" -ForegroundColor Cyan

$collector = Join-Path $InstallDir 'win-metrics.ps1'
Assert (Test-Path $collector) "采集脚本已安装到 $collector"
if (Test-Path $collector) {
    $txt = Get-Content $collector -Raw
    Assert ($txt -match 'param\(') "采集脚本是完整脚本（含 param 块）"
    Assert ($txt -match 'Resolve-MacEndpoint') "采集脚本含 Mac 自动发现逻辑"
}

Assert ($null -ne $global:REGISTERED) "注册了计划任务"
if ($global:REGISTERED) {
    $r = $global:REGISTERED
    Assert ($r.TaskName -eq 'win-metrics') "任务名 = $($r.TaskName)"
    Assert ($r.Trigger.AtStartup) "触发器 = AtStartup（开机自启）"
    Assert ($r.Principal.UserId -eq 'SYSTEM') "运行账号 = $($r.Principal.UserId)"
    Assert ($r.Principal.RunLevel -eq 'Highest') "权限 = $($r.Principal.RunLevel)"
    Assert ($null -ne $r.Settings.ExecutionTimeLimit -and
        $r.Settings.ExecutionTimeLimit.TotalSeconds -eq 0) `
        "ExecutionTimeLimit=0（常驻脚本不能被默认时长掐掉）"
    Assert ($r.Action.Argument -match '-File\s+"?[^"]*win-metrics\.ps1') `
        "任务参数含 -File win-metrics.ps1"
    Assert ($r.Action.Argument -match '-ExecutionPolicy Bypass') `
        "任务参数含 -ExecutionPolicy Bypass"
    Assert ($r.Action.Execute -match 'pwsh(\.exe)?$') "执行的是 pwsh（$($r.Action.Execute)）"
}

# 端口参数要真的进到 URL 里（曾经因为 $Port 未定义导致 URL 变成 host:/path，
# 下载直接失败 —— 上面「采集脚本已安装」这条断言就已经覆盖了）
Assert ($allText -match '采集脚本已(安装|就位)') "安装器自己报告采集脚本已落地"
Assert ($allText -notmatch '下载失败') "下载没有失败"

Write-Host ""
if ($fails.Count -eq 0) {
    Write-Host "== 演练全部通过 ==" -ForegroundColor Green
    Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
    exit 0
} else {
    Write-Host "== 演练失败 $($fails.Count) 项 ==" -ForegroundColor Red
    $fails | ForEach-Object { Write-Host "  - $_" }
    Write-Host "（临时目录保留在 $InstallDir 供排查）"
    exit 1
}
