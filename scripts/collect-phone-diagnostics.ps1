param(
    [Parameter(Mandatory = $true)][string]$Serial,
    [string]$Adb = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$OutputDirectory = (Join-Path $env:LOCALAPPDATA ('IndustryDayDrone\logs\phone-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'load-local-settings.ps1')
Assert-DroneNonSyncedPath -Path $OutputDirectory
$root = (Split-Path $PSScriptRoot -Parent).TrimEnd('\') + '\'
if ([IO.Path]::GetFullPath($OutputDirectory).StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Phone logs must stay outside the repository.'
}
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Choose a new output directory to preserve previous logs.' }
$state = & $Adb -s $Serial get-state
if ($LASTEXITCODE -ne 0 -or $state -ne 'device') { throw 'Authorize this computer for ADB on the phone first.' }
$files = @(& $Adb -s $Serial shell run-as com.ms.voice ls files/field-diagnostics)
if ($LASTEXITCODE -ne 0) { throw 'Private diagnostic files are unavailable. Run the diagnostic build first.' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
foreach ($file in $files) {
    $name = $file.Trim()
    if ($name -notmatch '^field-[0-3]\.jsonl$') { continue }
    $content = @(& $Adb -s $Serial shell run-as com.ms.voice cat "files/field-diagnostics/$name")
    if ($LASTEXITCODE -ne 0) { throw "Could not read $name. Previous files remain intact." }
    [IO.File]::WriteAllLines((Join-Path $OutputDirectory $name), [string[]]$content, [Text.UTF8Encoding]::new($false))
}
Write-Output "Private phone diagnostics saved to $OutputDirectory"
Write-Output 'No app restart, data clearing, SDK reset or flight command was sent.'
