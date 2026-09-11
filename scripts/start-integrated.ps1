param(
    [Parameter(Mandatory = $true)][ValidateSet('Test','Real')][string]$Mode,
    [string]$EnvFile = (Join-Path $env:LOCALAPPDATA 'IndustryDayDrone\config\field-live.env'),
    [switch]$CheckOnly,
    [switch]$NoWeb,
    [ValidateRange(1024,65535)][int]$RelayPort = 8080,
    [ValidateRange(1024,65535)][int]$WebPort = 3000,
    [string]$RelayPython = '',
    [string]$ControlPython = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'load-local-settings.ps1')
$modeValue = $Mode.ToLowerInvariant()
$real = $Mode -eq 'Real'
$toolEndpoint = if ($real) { 'http://127.0.0.1:8766' } else { 'inprocess://drone-tools/v1' }
if ($RelayPort -in @(8766,9997,9998,9999) -or $WebPort -in @(8766,9997,9998,9999) -or
    (-not $NoWeb -and $WebPort -eq $RelayPort)) {
    throw 'Relay and web ports must be distinct and cannot use PC or phone control ports.'
}
if (-not $RelayPython) { $RelayPython = Join-Path $root 'relay\.venv\Scripts\python.exe' }
if (-not $ControlPython) { $ControlPython = Join-Path $root 'drone-control\.venv\Scripts\python.exe' }
$node = if (-not $NoWeb) { (Get-Command node -ErrorAction Stop).Source } else { $null }
if (-not (Test-Path -LiteralPath $RelayPython -PathType Leaf)) { throw 'Create relay\.venv and install its existing requirements first.' }
if ($real -and -not (Test-Path -LiteralPath $ControlPython -PathType Leaf)) { throw 'Create the PC controller environment before Real mode.' }
if (-not $NoWeb -and -not (Test-Path -LiteralPath (Join-Path $root '.next\standalone\server.js'))) {
    throw 'Build this UI with its local relay URL before starting the web server.'
}
$settings = @{}
if (Test-Path -LiteralPath $EnvFile -PathType Leaf) { $settings = Import-DroneLocalSettings -Path $EnvFile }
elseif ($real) { throw 'Real mode requires the existing private connection environment file.' }
$saved = @{}
$processes = @()
function Start-NodeProcess {
    param([string[]]$Arguments, [string]$OutFile, [string]$ErrFile)
    $private = @{}
    try {
        foreach ($key in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
            if ($key -match 'TOKEN|SECRET|PASSWORD|API_KEY|CONFIRMATION|CONNECTION_STRING|^(DRONE_|RELAY_|VOICE_|AZURE_)') {
                $private[$key] = [Environment]::GetEnvironmentVariable($key,'Process')
                [Environment]::SetEnvironmentVariable($key,$null,'Process')
            }
        }
        Start-Process $node -ArgumentList $Arguments -WorkingDirectory $root -NoNewWindow -PassThru `
            -RedirectStandardOutput $OutFile -RedirectStandardError $ErrFile
    } finally {
        foreach ($key in $private.Keys) { [Environment]::SetEnvironmentVariable($key,$private[$key],'Process') }
    }
}
$connection = @{}
$backendNames = '^(DRONE_|RELAY_|TRIAGE_MODE$|VOICE_LIVE_RESOURCE$|VOICE_LIVE_REGION$|AZURE_VISION_)'
foreach ($key in $settings.Keys) {
    if ($key -match $backendNames) { $connection[$key] = $settings[$key] }
}
# Keep the selected UI branch's voice/persona/VAD defaults; only connection settings are imported.
$connection['DRONE_RUN_MODE'] = $modeValue
$connection['DRONE_REAL_TRANSPORT'] = 'local'
$connection['DRONE_CONTROL_TRANSPORT'] = 'local'
$connection['DRONE_CONTROL_USE_TOOLS'] = '1'
$connection['RELAY_HOST'] = '127.0.0.1'
$connection['RELAY_PORT'] = [string]$RelayPort
$connection['RELAY_LOCAL_DIRECT'] = '1'
$connection.Remove('DRONE_TEST_API_URL')
$connection.Remove('DRONE_TEST_API_TOKEN')
$connection['DRONE_CONTROL_MODE'] = if ($real) { 'live' } else { 'mock' }
$connection['TRIAGE_MODE'] = if ($real) { 'azure' } else { 'mock' }
$connection['DRONE_CONTROL_ENABLE_LIVE'] = if ($real) { '1' } else { '0' }
$connection['DRONE_CONTROL_MOCK_CAPTURES'] = '0'
$connection['DRONE_CONTROL_READ_ONLY'] = '0'
$connection['DRONE_CONTROL_ADAPTER'] = 'field'
$connection['DRONE_CONTROL_PORT'] = '8766'
if (-not $real) {
    foreach ($key in @('DRONE_CONTROL_API_TOKEN','DRONE_CONTROL_CONFIG_PATH','DRONE_CONTROL_SITE_CONFIG',
                       'DRONE_CONTROL_FIELD_PROFILE','DRONE_CONTROL_FIELD_REFERENCE')) { $connection.Remove($key) }
}
$connection['DRONE_CONTROL_API_URL'] = $toolEndpoint
if ($real) {
    $connection['DRONE_CONTROL_FIELD_PROFILE'] = Join-Path $root 'drone-control\trials\profiles\standalone_tag_6321236.json'
    $connection['DRONE_CONTROL_FIELD_REFERENCE'] = Join-Path $root 'drone-control\trials\profiles\id1_tv_pair_reference.json'
}
$logRoot = Join-Path $env:LOCALAPPDATA ('IndustryDayDrone\logs\integrated\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
Push-Location $root
try {
    foreach ($key in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
        if ($key -match '^(DRONE_|RELAY_|TRIAGE_MODE$|VOICE_|AZURE_VISION_)') {
            $saved[$key] = [Environment]::GetEnvironmentVariable($key,'Process')
            [Environment]::SetEnvironmentVariable($key,$null,'Process')
        }
    }
    foreach ($key in $connection.Keys) {
        if (-not $saved.ContainsKey($key)) { $saved[$key] = [Environment]::GetEnvironmentVariable($key,'Process') }
        [Environment]::SetEnvironmentVariable($key,$connection[$key],'Process')
    }
    foreach ($key in @('PYTHONPATH','HOSTNAME','PORT','NEXT_TELEMETRY_DISABLED')) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key,'Process')
    }
    $env:PYTHONPATH = (Join-Path $root 'drone-control\pc') + ';' + $root
    $env:NEXT_TELEMETRY_DISABLED = '1'
    & $RelayPython -B -c "from relay import config; from relay.drone_client import DroneClient; c=DroneClient('preflight'); error=c.readiness(); print('runMode='+config.DRONE_RUN_MODE+'; tools='+c.base_url+'; wireMode='+config.DRONE_CONTROL_MODE); raise SystemExit(error if error else 0)"
    if ($LASTEXITCODE -ne 0) { throw 'Selected backend target is not configured. No process was started.' }
    if ($real) {
        foreach ($key in @('DRONE_CONTROL_CONFIG_PATH','DRONE_CONTROL_SITE_CONFIG')) {
            $path = [string]$connection[$key]
            if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Real mode requires $key" }
            Assert-DroneNonSyncedPath -Path $path
        }
        & $ControlPython -B -c "from drone_nav.tool_control.server import adapter_from_environment; a=adapter_from_environment(); print('real profile ready='+str(a.live_ready)+'; no hardware commands sent'); a.video_broker.close()"
        if ($LASTEXITCODE -ne 0) { throw 'The actual PC profile is not valid.' }
    } else {
        & $RelayPython -B -c "import asyncio; from relay.drone_client import DroneClient; r=asyncio.run(DroneClient('preflight').call('drone_get_capabilities', {})); assert r['execution_mode']=='mock' and r['physical_execution'] is False; print('Embedded mock ready; no additional process or port.')"
        if ($LASTEXITCODE -ne 0) { throw 'The embedded mock contract is not ready.' }
    }
    if ($CheckOnly) { Write-Output 'Configuration-only preflight finished. This does not prove current aircraft readiness.'; return }
    $ports = @($RelayPort)
    if ($real) { $ports += 8766 }
    if (-not $NoWeb) { $ports += $WebPort }
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($port in $ports) {
        if ($listeners.Port -contains $port) { throw "Port $port is occupied. End the old session before changing modes; it was not stopped." }
    }
    [IO.Directory]::CreateDirectory($logRoot) | Out-Null
    if ($real) {
        $processes += Start-Process $ControlPython -ArgumentList '-B -m drone_nav.tool_control.server' `
            -WorkingDirectory $root -NoNewWindow -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot 'tools.out.log') -RedirectStandardError (Join-Path $logRoot 'tools.err.log')
    }
    $processes += Start-Process $RelayPython -ArgumentList '-B -m relay.server' -WorkingDirectory $root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $logRoot 'relay.out.log') -RedirectStandardError (Join-Path $logRoot 'relay.err.log')
    if (-not $NoWeb) {
        $standalone = Join-Path $root '.next\standalone'
        foreach ($asset in @('public','.next\static')) {
            $destination = Join-Path $standalone $asset
            [IO.Directory]::CreateDirectory($destination) | Out-Null
            Get-ChildItem -LiteralPath (Join-Path $root $asset) -Force | Copy-Item -Destination $destination -Recurse -Force
        }
        $env:HOSTNAME = '127.0.0.1'
        $env:PORT = [string]$WebPort
        $webFile = Join-Path $standalone 'server.js'
        $processes += Start-NodeProcess -Arguments @("`"$webFile`"") `
            -OutFile (Join-Path $logRoot 'web.out.log') -ErrFile (Join-Path $logRoot 'web.err.log')
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ($true) {
        foreach ($process in $processes) { if ($process.HasExited) { throw "Component exited (PID $($process.Id)); see $logRoot" } }
        try {
            $reply = Invoke-WebRequest "http://127.0.0.1:$RelayPort/api/config" -UseBasicParsing -TimeoutSec 5
            $info = [Text.Encoding]::UTF8.GetString($reply.RawContentStream.ToArray()) | ConvertFrom-Json
            if (-not $NoWeb) { $null = Invoke-WebRequest "http://127.0.0.1:$WebPort" -UseBasicParsing -TimeoutSec 5 }
        } catch [Net.WebException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Selected stack did not become responsive. See local logs.' }
            Start-Sleep -Milliseconds 200
            continue
        }
        if ($info.runMode -ne $modeValue) { throw 'Running relay mode differs from requested mode.' }
        break
    }
    Write-Output "$Mode stack running; relay=http://127.0.0.1:$RelayPort; tools=$toolEndpoint. No mission started."
    if (-not $NoWeb) { Write-Output "UI=http://127.0.0.1:$WebPort (same build for Test and Real)" }
    if (-not $info.droneReady) { Write-Warning $info.droneError }
    while ($true) {
        foreach ($process in $processes) { if ($process.HasExited) { throw "Component exited (PID $($process.Id)); see $logRoot" } }
        Start-Sleep -Seconds 1
    }
} finally {
    foreach ($process in $processes) { if (-not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction Continue } }
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key,$saved[$key],'Process') }
    $settings = $null; $connection = $null
    Pop-Location
}
