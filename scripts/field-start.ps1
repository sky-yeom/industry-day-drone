# Field start: find the phone IP and launch the stack.
#
# Keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less UTF-8 as the
# ANSI code page, so any Korean text here prints as mojibake.
#
# Why:
#   The hotspot subnet changes on every connect, so network.host in
#   field-live-nav.json is always stale. On 2026-09-17 this cost 15 minutes with
#   the aircraft unable to connect. Find the address here instead of asking.
#
# Search order:
#   1) Wi-Fi default gateway. When the phone is the hotspot it is the gateway.
#   2) The address left in the config file.
#   3) Full scan of the same /24 (parallel).
#   A phone is detected by a TCP connect to port 9998, not ping (it ignores ping).
#
# Usage:
#   .\scripts\field-start.ps1              # find and launch (keep this window open)
#   .\scripts\field-start.ps1 -PhoneIp x   # give the address directly
#   .\scripts\field-start.ps1 -NoStart     # only find the address and update config
#
# This script does not check readiness; this window is held while the stack runs.
# From another window run:
#   Invoke-RestMethod http://127.0.0.1:8080/api/drone/status
#
# 2026-09-17: the stack used to run under Start-Job, which dies with its parent
# session, so the stack vanished when the script ended. start-integrated.ps1 now
# runs in the foreground: this window is the lifetime of the stack.

[CmdletBinding()]
param(
    [string]$PhoneIp = '',
    [int]$ControlPort = 9998,
    [int]$VideoPort = 9999,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$other  = 'C:\Users\t-hajongkim\source\repos\industry-day-drone'
$navCfg = Join-Path $env:LOCALAPPDATA 'IndustryDayDrone\config\field-live-nav.json'

# --- Phone check: trust TCP only --------------------------------------------
function Test-Phone {
    param([string]$Address, [int]$TimeoutMs = 400)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        if (-not $client.ConnectAsync($Address, $ControlPort).Wait($TimeoutMs)) { return $false }
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Find-Phone {
    $wifi = Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -match 'Wi-?Fi|Wireless' } | Select-Object -First 1
    if (-not $wifi) { throw 'No Wi-Fi adapter is connected. Join the phone hotspot first.' }
    $addr = Get-NetIPAddress -InterfaceIndex $wifi.ifIndex -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    $ssid = (netsh wlan show interfaces | Select-String '^\s*SSID\s*:\s*(.+)$').Matches.Groups[1].Value.Trim()
    # A Korean SSID also prints as mojibake in this console, so hide non-ASCII names.
    if ($ssid -match '[^\x20-\x7E]') { $ssid = '<non-ASCII name>' }
    Write-Host "PC   : $($addr.IPAddress)  (SSID $ssid)"

    # 1) Gateway = hotspot phone
    $gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceIndex $wifi.ifIndex -ErrorAction SilentlyContinue | Select-Object -First 1).NextHop
    if ($gw -and (Test-Phone $gw)) { Write-Host "Phone: $gw  (gateway)"; return $gw }

    # 2) Address left in the config file
    if (Test-Path -LiteralPath $navCfg) {
        $known = (Get-Content -LiteralPath $navCfg -Raw | ConvertFrom-Json).network.host
        if ($known -and (Test-Phone $known)) { Write-Host "Phone: $known  (previous config)"; return $known }
    }

    # 3) Full scan of the same /24
    $prefix = ($addr.IPAddress -split '\.')[0..2] -join '.'
    Write-Host "Phone: scanning $prefix.0/24 ..."
    $jobs = 1..254 | ForEach-Object {
        Start-ThreadJob -ScriptBlock {
            param($ip, $port)
            $c = [System.Net.Sockets.TcpClient]::new()
            try { if ($c.ConnectAsync($ip, $port).Wait(700) -and $c.Connected) { $ip } } catch { } finally { $c.Dispose() }
        } -ArgumentList "$prefix.$_", $ControlPort
    }
    $hit = ($jobs | Wait-Job -Timeout 40 | Receive-Job | Where-Object { $_ } | Select-Object -First 1)
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    if ($hit) { Write-Host "Phone: $hit  (scan)"; return $hit }
    throw "Phone not found. Check that the bridge app is running and on the same hotspot (port $ControlPort)."
}

# --- Resolve address ---------------------------------------------------------
if ($PhoneIp) {
    if (-not (Test-Phone $PhoneIp)) { throw "Port $ControlPort on $PhoneIp is not answering." }
    Write-Host "Phone: $PhoneIp  (given)"
    $phone = $PhoneIp
} else {
    $phone = Find-Phone
}

# --- Update config -----------------------------------------------------------
if (-not (Test-Path -LiteralPath $navCfg)) { throw "Config file missing: $navCfg" }
$cfg  = Get-Content -LiteralPath $navCfg -Raw | ConvertFrom-Json
$was  = $cfg.network.host
$cfg.network.host       = $phone
$cfg.network.port       = $ControlPort
$cfg.network.video_port = $VideoPort
# A BOM breaks Python json.load, so write UTF8Encoding($false).
[System.IO.File]::WriteAllText($navCfg, ($cfg | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding $false))
Write-Host "Config: network.host $was -> $phone"

if ($NoStart) { return }

# --- Stop the previous stack -------------------------------------------------
$owners = @()
foreach ($p in 8766, 8080, 3000) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    if ($conn) { $owners += $conn.OwningProcess }
}
foreach ($id in ($owners | Sort-Object -Unique)) {
    try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host "Stopped: pid $id" } catch { }
}
if ($owners) { Start-Sleep -Seconds 3 }

# --- Launch ------------------------------------------------------------------
# Foreground run: closing this window ends the stack. Check readiness from
# another window with Invoke-RestMethod http://127.0.0.1:8080/api/drone/status.
Write-Host ''
Write-Host 'Starting... (keep this window open; closing it stops the stack)'
Write-Host '  Readiness (other window): Invoke-RestMethod http://127.0.0.1:8080/api/drone/status'
Write-Host '  Control UI              : http://127.0.0.1:3000'
Write-Host "  Flight logs             : $env:LOCALAPPDATA\IndustryDayDrone\logs\navigation"
Write-Host ''

& (Join-Path $root 'scripts\start-integrated.ps1') -Mode Real `
    -RelayPython   "$other\relay\.venv\Scripts\python.exe" `
    -ControlPython "$other\drone-control\.venv\Scripts\python.exe"
