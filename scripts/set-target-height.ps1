<#
.SYNOPSIS
    Shows or sets the ascent target height in the flight profile.

.DESCRIPTION
    The climb target is a control tunable, so it lives in the flight profile beside
    the tilt, hold and timeout values it has to agree with, and nowhere else. This
    script exists because the profile the running stack loads is named by
    DRONE_CONTROL_FIELD_PROFILE and may sit in a different clone than the one you
    are editing, which is how a careful edit ends up having no effect.

    Values outside the band the bounded climb law was validated for are refused
    here rather than mid-ascent, and anything written is backed up first.

.EXAMPLE
    .\set-target-height.ps1
    Shows the current target height in every profile that matters.

.EXAMPLE
    .\set-target-height.ps1 -Meters 1.5
    Sets them to 1.5 m.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [double] $Meters,
    [string] $Env = "$env:LOCALAPPDATA\IndustryDayDrone\config\field-live.env"
)

$ErrorActionPreference = 'Stop'

# Keep in step with FieldAdapter.target_height_band_m and load_profile's bounds.
$BandLow, $BandHigh = 1.4, 1.6

function Read-Json([string] $Path) {
    return (Get-Content -Raw -LiteralPath $Path) -replace '^\uFEFF', '' | ConvertFrom-Json
}

$paths = New-Object System.Collections.ArrayList
if (Test-Path $Env) {
    $line = Select-String -Path $Env -Pattern '^\s*DRONE_CONTROL_FIELD_PROFILE\s*=\s*(.+)$' |
        Select-Object -First 1
    if ($line) {
        $configured = $line.Matches[0].Groups[1].Value.Trim().Trim('"')
        if (Test-Path $configured) { [void]$paths.Add((Resolve-Path $configured).Path) }
        else { Write-Warning "DRONE_CONTROL_FIELD_PROFILE points at a missing file: $configured" }
    }
}
# The copy in this checkout is the version-controlled source of truth, so it is
# kept in step even when the stack runs from another clone.
$here = Join-Path $PSScriptRoot '..\drone-control\trials\profiles\standalone_tag_6321236.json'
if (Test-Path $here) {
    $resolved = (Resolve-Path $here).Path
    if ($paths -notcontains $resolved) { [void]$paths.Add($resolved) }
}
if ($paths.Count -eq 0) { throw 'No flight profile found.' }

Write-Host 'Ascent target height (profile is the only place it is written):'
$current = @{}
foreach ($path in $paths) {
    $current[$path] = (Read-Json $path).target_height_m
    "  {0,5} m   {1}" -f $current[$path], $path | Write-Host
}

$distinct = $current.Values | Sort-Object -Unique
if ($distinct.Count -gt 1) {
    Write-Host ''
    Write-Warning ("These clones disagree ({0}); the running stack uses the first one listed." -f ($distinct -join ' vs '))
}

if (-not $PSBoundParameters.ContainsKey('Meters')) {
    Write-Host ''
    Write-Host "Pass -Meters <$BandLow..$BandHigh> to set it."
    return
}
if ($Meters -lt $BandLow -or $Meters -gt $BandHigh) {
    throw "Target height must be within $BandLow..$BandHigh m; the climb controller rejects anything else."
}

Write-Host ''
$changed = $false
foreach ($path in $paths) {
    if ($current[$path] -eq $Meters) { Write-Host "unchanged  $path"; continue }
    if (-not $PSCmdlet.ShouldProcess($path, "set target_height_m = $Meters")) { continue }
    $backup = "$path.$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
    Copy-Item -LiteralPath $path -Destination $backup
    $json = Read-Json $path
    $json.target_height_m = $Meters
    # Python reads these with utf-8-sig, but Node does not, so never write a BOM.
    [System.IO.File]::WriteAllText($path, ($json | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding $false))
    Write-Host "updated    $path  $($current[$path]) -> $Meters"
    Write-Host "  backup   $backup"
    $changed = $true
}

if ($changed) {
    Write-Host ''
    Write-Host 'The stack reads configuration once at startup, so restart it now:'
    Write-Host '  .\scripts\start-integrated.ps1'
}
