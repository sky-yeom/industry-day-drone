param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('control', 'relay', 'dashboard')][string]$Component,
    [string[]]$EnvFile = @(),
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
foreach ($envPath in $EnvFile) {
    foreach ($line in [IO.File]::ReadAllLines((Resolve-Path -LiteralPath $envPath).Path)) {
        $entry = $line.Trim()
        if (-not $entry -or $entry.StartsWith('#')) { continue }
        if ($entry -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw 'Environment file must contain KEY=value lines.' }
        $entryName, $entryValue = $Matches[1], $Matches[2].Trim()
        if ($entryValue.Length -ge 2 -and (($entryValue.StartsWith('"') -and $entryValue.EndsWith('"')) -or ($entryValue.StartsWith("'") -and $entryValue.EndsWith("'")))) {
            $entryValue = $entryValue.Substring(1, $entryValue.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($entryName, $entryValue, 'Process')
    }
}
Push-Location $repoRoot
try {
    if ($Component -eq 'dashboard') {
        $nodePath = (Get-Command node -ErrorAction Stop).Source
        & $nodePath (Join-Path $repoRoot 'node_modules/next/dist/bin/next') dev
    } else {
        if (-not $PythonPath) {
            $venvDir = if ($Component -eq 'relay') { 'relay' } else { 'drone-control' }
            $PythonPath = Join-Path $repoRoot "$venvDir/.venv/Scripts/python.exe"
        }
        if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Python environment missing. Follow drone-control/docs/CONTROL_INTEGRATION_20260910.md.' }
        if ($Component -eq 'relay') {
            & $PythonPath (Join-Path $repoRoot 'relay/server.py')
        } else {
            $env:PYTHONPATH = Join-Path $repoRoot 'drone-control/pc'
            & $PythonPath -m drone_nav.tool_control.server
        }
    }
    $componentExit = $LASTEXITCODE
} finally { Pop-Location }
exit $componentExit
