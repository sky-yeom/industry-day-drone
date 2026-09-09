param(
    [Parameter(Mandatory = $true)][string]$PhoneIp,
    [switch]$Execute,
    [string]$ConfigPath = 'C:\dev\13_DRONE\pc\config.local.json'
)
$ErrorActionPreference = 'Stop'
$runnerPath = Join-Path $PSScriptRoot 'coex_tagless_left_return.py'
$projectPython = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'
$existingPython = 'C:\dev\13_DRONE\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $projectPython) {
    $pythonPath = $projectPython
} elseif (Test-Path -LiteralPath $existingPython) {
    $pythonPath = $existingPython
} else {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
}
$runnerArgs = @('-B', $runnerPath, '--host', $PhoneIp, '--config', $ConfigPath)
if ($Execute) {
    Write-Host 'One supervised flight: 1.8m height -> 1m left -> return -> RC landing.'
    Write-Host 'Execute means the route is clear, sensors uncovered, and the RC pilot is ready.'
    $runnerArgs += @('--execute', '--site-ready')
} else {
    Write-Host 'Ground check only. No takeoff or arm commands.'
    $runnerArgs += '--check'
}
& $pythonPath @runnerArgs
exit $LASTEXITCODE
