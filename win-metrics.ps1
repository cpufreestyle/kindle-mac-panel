#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Windows 主机指标采集器 —— 把 CPU/内存/磁盘/网速 POST 到 Mac 的运维值班台面板。

.DESCRIPTION
    配合 kindle-panel/server.py 使用：server.py 暴露 POST /api/push/win，
    本脚本每 5 秒采集一次并推送 JSON；Mac 端超过 30s 没收到就判定离线。

    采集项：CPU 总占用、内存、磁盘（系统盘）、网络收发速率、CPU 温度（可读时）。

.EXAMPLE
    pwsh -NoProfile -File win-metrics.ps1 -MacIp 192.168.1.112
    # 前台跑一次看输出：
    pwsh -NoProfile -File win-metrics.ps1 -MacIp 192.168.1.112 -Once
#>
[CmdletBinding()]
param(
    # 接收端（Mac 的局域网 IP）。留空则自动发现（Mac 换网段时不会失联）
    [string]$MacIp = '',
    [int]$Port = 18866,
    # 采集间隔（秒）
    [int]$IntervalSec = 5,
    # 只采一次就退出（调试用）
    [switch]$Once,
    # 只做诊断：探测 Mac、跑一次采集、报告每项结果，不循环
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

# Mac 候选地址（Mac 的 en0 IP 随网络在 192.168.1.x / 192.168.15.x 之间切换，
# 写死单个 IP 会在换网后失联 —— 与 kindle-panel/fetchloop-v10.sh 同样的坑）
$MacCandidates = @(
    '192.168.1.112', '192.168.15.201', '192.168.1.136', '192.168.1.191',
    '192.168.1.224', '192.168.15.244'
)
$CacheFile = "$env:LOCALAPPDATA\win-metrics\mac-ip.txt"
$endpoint = ''

function Resolve-MacEndpoint {
    # 返回可达的 endpoint；探测顺序：显式 -MacIp → 缓存 → 候选列表
    $cands = @()
    if ($MacIp) { $cands += $MacIp }
    try {
        if (Test-Path $CacheFile) {
            $cached = (Get-Content $CacheFile -ErrorAction Stop | Select-Object -First 1)
            if ($cached) { $cands += $cached.Trim() }
        }
    } catch { }
    $cands += $MacCandidates

    $seen = @{}
    foreach ($c in $cands) {
        if (-not $c -or $seen.ContainsKey($c)) { continue }
        $seen[$c] = $true
        $ep = "http://${c}:${Port}/api/push/win"
        try {
            # 用 /lan.txt 探活（Mac 侧必定返回本机 IP）
            $r = Invoke-WebRequest -Uri "http://${c}:${Port}/lan.txt" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200 -and $r.Content) {
                try {
                    $dir = Split-Path $CacheFile -Parent
                    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
                    Set-Content -Path $CacheFile -Value $c
                } catch { }
                return @{ endpoint = $ep; ip = $c }
            }
        } catch { }
    }
    return $null
}

# ── 采集工具函数 ────────────────────────────────────────────────────────────

function Get-CpuUsagePercent {
    # 首选 Get-Counter：自带 1 秒采样窗口，值总是准的。
    # WMI Formatted 类需要「两次采样」才准（第一次读可能是陈值/乱值），不能做首选。
    try {
        $c = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1
        return [math]::Round($c.CounterSamples[0].CookedValue, 1)
    } catch { }
    # 回退：WMI Formatted（_Total 是 0–100 的整机占用，不是各核求和）
    try {
        $p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop
        if ($p) { return [math]::Round([double]$p.PercentProcessorTime, 1) }
    } catch { }
    # 兜底：LoadPercentage（部分机器不准，仅作保底）
    try {
        $s = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average
        return [math]::Round($s.Average, 1)
    } catch { return $null }
}

function Get-MemInfo {
    # 必须自带 try/catch：脚本设了 $ErrorActionPreference='Stop'，
    # 裸的 Get-CimInstance 失败会直接终止整个采集循环。
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $total = [double]$os.TotalVisibleMemorySize * 1KB
        $free = [double]$os.FreePhysicalMemory * 1KB
        $used = $total - $free
        return @{
            usedGB  = [math]::Round($used / 1GB, 2)
            totalGB = [math]::Round($total / 1GB, 2)
            pct     = $(if ($total -gt 0) { [math]::Round($used / $total * 100, 1) } else { $null })
        }
    } catch { return @{} }
}

function Get-DiskInfo {
    # 系统盘（通常是 C:）
    $sysDrive = $env:SystemDrive
    if (-not $sysDrive) { $sysDrive = 'C:' }
    try {
        $d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$sysDrive'" -ErrorAction Stop
        $total = [double]$d.Size
        $free = [double]$d.FreeSpace
        $used = $total - $free
        return @{
            usedGB  = [math]::Round($used / 1GB, 2)
            totalGB = [math]::Round($total / 1GB, 2)
            freeGB  = [math]::Round($free / 1GB, 2)
            pct     = $(if ($total -gt 0) { [math]::Round($used / $total * 100, 1) } else { $null })
        }
    } catch { return @{} }
}

function Get-CpuTempC {
    # CPU 温度：MSAcpi_ThermalZoneTemperature（很多机器读不到，读不到就返回 null）
    try {
        $t = Get-CimInstance -Namespace 'root/wmi' -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
            Select-Object -First 1
        if ($t) {
            # 温度以 1/10 K 表示，转换成摄氏度
            $c = ([double]$t.CurrentTemperature / 10.0) - 273.15
            if ($c -gt 0 -and $c -lt 120) { return [math]::Round($c, 1) }
        }
    } catch { }
    return $null
}

function Get-UptimeSeconds {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        # 括号必须包住 .TotalSeconds —— 写成 [int](...).TotalSeconds 会先转型
        # TimeSpan（抛异常）再取属性
        return [int](((Get-Date) - $os.LastBootUpTime).TotalSeconds)
    } catch { return $null }
}

# ── 主循环 ──────────────────────────────────────────────────────────────────

$prevBytes = $null
$prevTime = $null

# 解析接收端（-MacIp 给死就用它，否则自动发现）
$mac = Resolve-MacEndpoint
if (-not $mac) {
    Write-Host "win-metrics: 找不到可达的 Mac（候选: $($MacCandidates -join ', ')）"
    Write-Host "win-metrics: 用 -MacIp <Mac的IP> 显式指定，或确认 Mac 上 server.py 在跑"
    if ($Check) { exit 2 }
    exit 1
}
$endpoint = $mac.endpoint
Write-Host "win-metrics: 推送到 $endpoint（每 ${IntervalSec}s，Ctrl+C 退出）"

if ($Check) {
    # 诊断模式：把每项采集结果和推送结果都打出来，方便贴回来定位
    Write-Host "---- 诊断 ----"
    Write-Host ("主机名     : " + $env:COMPUTERNAME)
    Write-Host ("接收端     : " + $endpoint)
    Write-Host ("CPU        : " + (Get-CpuUsagePercent))
    $m = Get-MemInfo
    Write-Host ("内存       : total=$($m.totalGB)G used=$($m.usedGB)G pct=$($m.pct)")
    $d = Get-DiskInfo
    Write-Host ("磁盘       : total=$($d.totalGB)G used=$($d.usedGB)G free=$($d.freeGB)G pct=$($d.pct)")
    Write-Host ("温度       : " + (Get-CpuTempC))
    Write-Host ("运行时长   : " + (Get-UptimeSeconds))
    $payload = @{
        host = $env:COMPUTERNAME; cpu = (Get-CpuUsagePercent)
        memPct = $m.pct; memUsedGB = $m.usedGB; memTotalGB = $m.totalGB
        diskPct = $d.pct; diskUsedGB = $d.usedGB; diskTotalGB = $d.totalGB
        diskFreeGB = $d.freeGB; tempC = (Get-CpuTempC)
        uptimeSec = (Get-UptimeSeconds)
    }
    $clean = [ordered]@{}
    foreach ($k in $payload.Keys) { if ($null -ne $payload[$k]) { $clean[$k] = $payload[$k] } }
    $body = ConvertTo-Json -InputObject $clean -Compress -Depth 3
    Write-Host ("推送内容   : " + $body)
    try {
        $resp = Invoke-WebRequest -Uri $endpoint -Method Post -Body $body `
            -ContentType 'application/json' -TimeoutSec 5 -UseBasicParsing
        Write-Host ("推送结果   : HTTP " + $resp.StatusCode + " " + $resp.Content)
    } catch {
        Write-Host ("推送结果   : 失败 " + $_.Exception.Message)
        exit 3
    }
    exit 0
}

while ($true) {
    $now = Get-Date
    $cpu = Get-CpuUsagePercent
    $mem = Get-MemInfo
    $disk = Get-DiskInfo

    # 网速：本次累计字节数 - 上次累计字节数，再除以时间差
    # 性能：perf 原始数据只查一次，再按网卡名匹配（原来每张网卡查一次，N+1 次 CIM）
    $downKBs = $null
    $upKBs = $null
    try {
        $adapters = Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True AND NetEnabled=True'
        if ($adapters) {
            $names = @($adapters | ForEach-Object { $_.NetConnectionID })
            $perf = Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface
            $cur = @{}
            foreach ($s in $perf) {
                if ($names -contains $s.Name) {
                    $cur[$s.Name] = @{
                        recv = [double]$s.BytesReceivedPersec
                        sent = [double]$s.BytesSentPersec
                    }
                }
            }
            if ($prevBytes -and $prevTime) {
                $dt = ($now - $prevTime).TotalSeconds
                if ($dt -gt 0) {
                $d = 0.0; $u = 0.0
                foreach ($k in $cur.Keys) {
                    if ($prevBytes.ContainsKey($k)) {
                        # BytesReceivedPersec/BytesSentPersec 是 32 位累计计数器，
                        # 高速链路上会回绕（4GB）—— 差分为负说明回绕了，加回 2^32
                        $dr = $cur[$k].recv - $prevBytes[$k].recv
                        if ($dr -lt 0) { $dr += 4294967296 }
                        $ds = $cur[$k].sent - $prevBytes[$k].sent
                        if ($ds -lt 0) { $ds += 4294967296 }
                        $d += $dr
                        $u += $ds
                    }
                }
                    $downKBs = [math]::Round($d / $dt / 1KB, 1)
                    $upKBs = [math]::Round($u / $dt / 1KB, 1)
                }
            }
            $prevBytes = $cur
            $prevTime = $now
        }
    } catch { }

    $payload = @{
        host       = $env:COMPUTERNAME
        cpu        = $cpu
        memPct     = $mem.pct
        memUsedGB  = $mem.usedGB
        memTotalGB = $mem.totalGB
        diskPct    = $disk.pct
        diskUsedGB = $disk.usedGB
        diskTotalGB = $disk.totalGB
        diskFreeGB = $disk.freeGB
        netDownKBs = $downKBs
        netUpKBs   = $upKBs
        tempC      = Get-CpuTempC
        uptimeSec  = Get-UptimeSeconds
    }
    # 去掉空值（server.py 只收数值字段）
    $clean = [ordered]@{}
    foreach ($k in $payload.Keys) {
        if ($null -ne $payload[$k]) { $clean[$k] = $payload[$k] }
    }
    # 用 -InputObject 而不是管道：管道对 OrderedDictionary 的处理依赖版本行为
    $body = ConvertTo-Json -InputObject $clean -Compress -Depth 3

    try {
        Invoke-RestMethod -Uri $endpoint -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 5 | Out-Null
        if ($Once) { Write-Host "win-metrics: 已推送 $body" }
    } catch {
        Write-Host "win-metrics: 推送失败 $($_.Exception.Message)"
        if ($Once) { Write-Host "win-metrics: 本次采集 $body" }
        # 连不上就重新发现 Mac（Mac 换网段/IP 漂移时自愈）
        if (-not $Once) {
            $m2 = Resolve-MacEndpoint
            if ($m2 -and $m2.endpoint -ne $endpoint) {
                $endpoint = $m2.endpoint
                Write-Host "win-metrics: 已切换到 $endpoint"
            }
        }
    }

    if ($Once) { break }

    Start-Sleep -Seconds $IntervalSec
}
