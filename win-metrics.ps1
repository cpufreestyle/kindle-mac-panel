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
    # 接收端（Mac 的局域网 IP）
    [string]$MacIp = '192.168.1.112',
    [int]$Port = 18866,
    # 采集间隔（秒）
    [int]$IntervalSec = 5,
    # 只采一次就退出（调试用）
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$endpoint = "http://${MacIp}:${Port}/api/push/win"

# ── 采集工具函数 ────────────────────────────────────────────────────────────

function Get-CpuUsagePercent {
    # 首选 PerfFormattedData（已算好的百分比，无需等待采样窗口）
    try {
        $p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop
        if ($p) { return [math]::Round([double]$p.PercentProcessorTime, 1) }
    } catch { }
    # 回退：Get-Counter（要等 1 秒采样窗口）
    try {
        $c = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1
        return [math]::Round($c.CounterSamples[0].CookedValue, 1)
    } catch {
        # 兜底：LoadPercentage（部分机器不准，仅作保底）
        try {
            $s = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average
            return [math]::Round($s.Average, 1)
        } catch { return $null }
    }
}

function Get-MemInfo {
    $os = Get-CimInstance Win32_OperatingSystem
    $total = [double]$os.TotalVisibleMemorySize * 1KB
    $free = [double]$os.FreePhysicalMemory * 1KB
    $used = $total - $free
    return @{
        usedGB  = [math]::Round($used / 1GB, 2)
        totalGB = [math]::Round($total / 1GB, 2)
        pct     = if ($total -gt 0) { [math]::Round($used / $total * 100, 1) } else { $null }
    }
}

function Get-DiskInfo {
    # 系统盘（通常是 C:）
    $sysDrive = $env:SystemDrive
    if (-not $sysDrive) { $sysDrive = 'C:' }
    $root = "$sysDrive\"
    try {
        $d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$sysDrive'"
        $total = [double]$d.Size
        $free = [double]$d.FreeSpace
        $used = $total - $free
        return @{
            usedGB  = [math]::Round($used / 1GB, 2)
            totalGB = [math]::Round($total / 1GB, 2)
            freeGB  = [math]::Round($free / 1GB, 2)
            pct     = if ($total -gt 0) { [math]::Round($used / $total * 100, 1) } else { $null }
        }
    } catch { return $null }
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
        return [int]((Get-Date) - $os.LastBootUpTime).TotalSeconds
    } catch { return $null }
}

# ── 主循环 ──────────────────────────────────────────────────────────────────

$prevBytes = $null
$prevTime = $null

Write-Host "win-metrics: 推送到 $endpoint（每 ${IntervalSec}s，Ctrl+C 退出）"

while ($true) {
    $now = Get-Date
    $cpu = Get-CpuUsagePercent
    $mem = Get-MemInfo
    $disk = Get-DiskInfo

    # 网速：本次字节数 - 上次字节数，再除以时间差
    $downKBs = $null
    $upKBs = $null
    try {
        $adapters = Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True AND NetEnabled=True'
        $cur = @{}
        foreach ($a in $adapters) {
            $s = Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface |
                Where-Object { $_.Name -eq $a.NetConnectionID }
            if ($s) {
                $cur[$a.NetConnectionID] = @{
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
                        $d += [math]::Max(0, $cur[$k].recv - $prevBytes[$k].recv)
                        $u += [math]::Max(0, $cur[$k].sent - $prevBytes[$k].sent)
                    }
                }
                $downKBs = [math]::Round($d / $dt / 1KB, 1)
                $upKBs = [math]::Round($u / $dt / 1KB, 1)
            }
        }
        $prevBytes = $cur
        $prevTime = $now
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
    $body = ($clean | ConvertTo-Json -Compress -Depth 3)

    if ($Once) {
        Write-Host $body
        break
    }

    try {
        Invoke-RestMethod -Uri $endpoint -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 5 | Out-Null
    } catch {
        Write-Host "win-metrics: 推送失败 $($_.Exception.Message)"
    }

    Start-Sleep -Seconds $IntervalSec
}
