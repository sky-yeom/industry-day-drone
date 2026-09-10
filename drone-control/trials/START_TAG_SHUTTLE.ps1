param(
    [ValidateSet('Plan', 'Camera', 'Flight')][string]$Mode = 'Plan',
    [string]$PhoneIp = '',
    [string]$ConfigPath = '',
    [string]$ProfilePath = '',
    [ValidateRange(1, 3600)][int]$CameraSeconds = 60,
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$droneRoot = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath = Join-Path $droneRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Install the drone-control Python environment first, or supply -PythonPath.'
}
if (-not $ProfilePath) { $ProfilePath = Join-Path $PSScriptRoot 'profiles/standalone_tag_6321236.json' }
if (-not $ConfigPath) { $ConfigPath = Join-Path $droneRoot 'pc/config.tag-shuttle.local.json' }
if ($Mode -ne 'Plan') {
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Supply -ConfigPath for your private camera/network configuration.' }
    $parsedAddress = $null
    if (-not [System.Net.IPAddress]::TryParse($PhoneIp, [ref]$parsedAddress)) {
        throw 'Supply the current phone IP using -PhoneIp. A previous PC address is not reused automatically.'
    }
}
$env:PYTHONIOENCODING = 'utf-8'
if ($Mode -eq 'Camera') {
    $runner = Join-Path $PSScriptRoot 'tag_camera_check.py'
    $runnerArgs = @('-B', $runner, '--config', $ConfigPath, '--host', $PhoneIp, '--duration', "$CameraSeconds", '--execute')
    Write-Host 'Camera only: read video and record AprilTags; no aircraft movement or gimbal commands.'
} else {
    $runner = Join-Path $PSScriptRoot 'standalone_tag_shuttle.py'
    $runnerArgs = @('-B', $runner, '--profile', $ProfilePath)
    if ($Mode -eq 'Flight') {
        $runnerArgs += @('--config', $ConfigPath, '--host', $PhoneIp, '--execute')
        Write-Host 'FLIGHT: takeoff -> 1.4m -> wall6 -> left 3,2,1 -> right 2,3,6 -> RC landing.'
    } else {
        Write-Host 'Plan only. No network or aircraft commands.'
    }
}
& $PythonPath @runnerArgs
exit $LASTEXITCODE
