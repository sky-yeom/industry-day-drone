# 현장 시작 — 폰 IP를 직접 찾아서 스택을 띄운다.
#
# 왜 있나:
#   핫스팟은 붙을 때마다 서브넷이 바뀐다. field-live-nav.json 의 network.host 는
#   지난번 그 주소라서 반드시 낡아 있다. 2026-09-17 아침에 이것 때문에 기기가
#   연결되지 못한 채 15분을 날렸다. 사람에게 IP를 묻지 말고 여기서 찾는다.
#
# 찾는 방법 (순서대로):
#   1) Wi-Fi 기본 게이트웨이 — 폰이 핫스팟이면 폰이 곧 게이트웨이다. 대부분 여기서 끝난다.
#   2) 설정 파일에 남아 있는 기존 주소
#   3) 같은 대역 전수 스캔 (/24, 병렬)
#   판정 기준은 ping 이 아니라 9998 포트 TCP 연결이다. 폰은 ping 에 답하지 않는다.
#
# 사용:
#   .\scripts\field-start.ps1              # 찾아서 띄운다 (이 창을 켜 둘 것)
#   .\scripts\field-start.ps1 -PhoneIp x   # 주소를 직접 준다
#   .\scripts\field-start.ps1 -NoStart     # 주소만 찾아서 설정만 고친다
#
# 준비 확인은 이 스크립트가 하지 않는다. 이 창은 스택이 사는 동안 계속 점유된다.
# 다른 창에서 아래를 실행하면 된다:
#   Invoke-RestMethod http://127.0.0.1:8080/api/drone/status
#
# 2026-09-17 수정: 예전에는 스택을 Start-Job 으로 띄웠다. 잡은 부모 세션이
# 끝나면 같이 죽는다. 그래서 스크립트가 끝나는 순간 스택도 사라졌고 "응답이
# 없다"는 오해를 샀다. 이제 start-integrated.ps1 을 전면(foreground)에서
# 실행한다. 이 창이 곧 스택의 수명이다.

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

# --- 폰 판정: TCP 만 믿는다 --------------------------------------------------
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
    if (-not $wifi) { throw 'Wi-Fi 어댑터가 연결돼 있지 않다. 핫스팟에 먼저 붙을 것.' }
    $addr = Get-NetIPAddress -InterfaceIndex $wifi.ifIndex -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    $ssid = (netsh wlan show interfaces | Select-String '^\s*SSID\s*:\s*(.+)$').Matches.Groups[1].Value.Trim()
    Write-Host "PC  : $($addr.IPAddress)  (SSID $ssid)"

    # 1) 게이트웨이 = 핫스팟 폰
    $gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceIndex $wifi.ifIndex -ErrorAction SilentlyContinue | Select-Object -First 1).NextHop
    if ($gw -and (Test-Phone $gw)) { Write-Host "폰  : $gw  (게이트웨이)"; return $gw }

    # 2) 설정 파일에 남아 있던 주소
    if (Test-Path -LiteralPath $navCfg) {
        $known = (Get-Content -LiteralPath $navCfg -Raw | ConvertFrom-Json).network.host
        if ($known -and (Test-Phone $known)) { Write-Host "폰  : $known  (기존 설정)"; return $known }
    }

    # 3) 같은 대역 전수 스캔
    $prefix = ($addr.IPAddress -split '\.')[0..2] -join '.'
    Write-Host "폰  : 탐색 중 $prefix.0/24 ..."
    $jobs = 1..254 | ForEach-Object {
        Start-ThreadJob -ScriptBlock {
            param($ip, $port)
            $c = [System.Net.Sockets.TcpClient]::new()
            try { if ($c.ConnectAsync($ip, $port).Wait(700) -and $c.Connected) { $ip } } catch { } finally { $c.Dispose() }
        } -ArgumentList "$prefix.$_", $ControlPort
    }
    $hit = ($jobs | Wait-Job -Timeout 40 | Receive-Job | Where-Object { $_ } | Select-Object -First 1)
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    if ($hit) { Write-Host "폰  : $hit  (스캔)"; return $hit }
    throw "폰을 못 찾았다. 폰에서 브리지 앱이 떠 있는지, 같은 핫스팟인지 확인할 것 ($ControlPort 포트)."
}

# --- 주소 확정 ---------------------------------------------------------------
if ($PhoneIp) {
    if (-not (Test-Phone $PhoneIp)) { throw "$PhoneIp 의 $ControlPort 포트가 응답하지 않는다." }
    Write-Host "폰  : $PhoneIp  (지정)"
    $phone = $PhoneIp
} else {
    $phone = Find-Phone
}

# --- 설정 반영 ---------------------------------------------------------------
if (-not (Test-Path -LiteralPath $navCfg)) { throw "설정 파일이 없다: $navCfg" }
$cfg  = Get-Content -LiteralPath $navCfg -Raw | ConvertFrom-Json
$was  = $cfg.network.host
$cfg.network.host       = $phone
$cfg.network.port       = $ControlPort
$cfg.network.video_port = $VideoPort
# BOM 이 붙으면 파이썬 json.load 가 깨진다. UTF8Encoding($false) 를 쓸 것.
[System.IO.File]::WriteAllText($navCfg, ($cfg | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding $false))
Write-Host "설정: network.host $was -> $phone"

if ($NoStart) { return }

# --- 기존 스택 정리 ----------------------------------------------------------
$owners = @()
foreach ($p in 8766, 8080, 3000) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    if ($conn) { $owners += $conn.OwningProcess }
}
foreach ($id in ($owners | Sort-Object -Unique)) {
    try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host "정리: pid $id" } catch { }
}
if ($owners) { Start-Sleep -Seconds 3 }

# --- 기동 --------------------------------------------------------------------
# 전면 실행이다. 이 창을 닫으면 스택도 끝난다. 준비 상태는 다른 창에서
# Invoke-RestMethod http://127.0.0.1:8080/api/drone/status 로 본다.
Write-Host ''
Write-Host '기동... (이 창을 켜 둘 것. 닫으면 스택도 끝난다)'
Write-Host '  준비 확인 (다른 창): Invoke-RestMethod http://127.0.0.1:8080/api/drone/status'
Write-Host '  관제 UI            : http://127.0.0.1:3000'
Write-Host "  비행 로그          : $env:LOCALAPPDATA\IndustryDayDrone\logs\navigation"
Write-Host ''

& (Join-Path $root 'scripts\start-integrated.ps1') -Mode Real `
    -RelayPython   "$other\relay\.venv\Scripts\python.exe" `
    -ControlPython "$other\drone-control\.venv\Scripts\python.exe"
