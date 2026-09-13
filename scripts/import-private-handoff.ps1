param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'IndustryDayDrone\handoff-config'),
    [string]$PhoneIp = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'load-local-settings.ps1')
$root = Split-Path $PSScriptRoot -Parent
foreach ($path in @($Bundle, $Destination)) { Assert-DroneNonSyncedPath -Path $path }
if ([IO.Path]::GetFullPath($Destination).StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Keep imported settings outside the repository.'
}
if (Test-Path -LiteralPath $Destination) { throw 'Destination exists. Choose a new path; current settings were not overwritten.' }
$source = Join-Path $Bundle 'config'
foreach ($name in @('field-live.env', 'field-live-nav.json', 'field-live-site.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $name))) { throw "Missing private handoff file: $name" }
}
$settings = Import-DroneLocalSettings -Path (Join-Path $source 'field-live.env')
$nav = [IO.File]::ReadAllText((Join-Path $source 'field-live-nav.json')) | ConvertFrom-Json
$site = [IO.File]::ReadAllText((Join-Path $source 'field-live-site.json')) | ConvertFrom-Json
if ($PhoneIp) {
    $address = $null
    if (-not [Net.IPAddress]::TryParse($PhoneIp, [ref]$address)) { throw 'PhoneIp must be an IP address.' }
    $nav.network.host = $PhoneIp
}
$build = [regex]::Match([IO.File]::ReadAllText((Join-Path $root 'drone-control\pc\drone_nav\tool_control\live.py')), 'BUILD_ID = "([^"]+)"')
if (-not $build.Success) { throw 'Cannot determine the required Android build from source.' }
$site.expected_bridge_build_id = $build.Groups[1].Value
$settings.DRONE_CONTROL_CONFIG_PATH = Join-Path $Destination 'field-live-nav.json'
$settings.DRONE_CONTROL_SITE_CONFIG = Join-Path $Destination 'field-live-site.json'
$settings.DRONE_CONTROL_DB = Join-Path $Destination 'data\field-tools.sqlite3'
$settings.DRONE_NAV_LOG_DIR = Join-Path $Destination 'logs\navigation'
$settings.DRONE_CONTROL_FIELD_PROFILE = Join-Path $root 'drone-control\trials\profiles\standalone_tag_6321236.json'
$settings.DRONE_CONTROL_FIELD_REFERENCE = Join-Path $root 'drone-control\trials\profiles\id1_tv_pair_reference.json'
New-Item -ItemType Directory -Path $Destination | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Destination 'data'),(Join-Path $Destination 'logs\navigation') | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $Destination 'field-live-nav.json'), ($nav | ConvertTo-Json -Depth 30), $utf8)
[IO.File]::WriteAllText((Join-Path $Destination 'field-live-site.json'), ($site | ConvertTo-Json -Depth 20), $utf8)
$lines = @($settings.Keys | Sort-Object | ForEach-Object { "$_=$($settings[$_])" })
[IO.File]::WriteAllLines((Join-Path $Destination 'field-live.env'), [string[]]$lines, $utf8)
$androidSource = Join-Path $Bundle 'android-private'
if (Test-Path -LiteralPath $androidSource) {
    $androidDestination = Join-Path $Destination 'android-private'
    Copy-Item -LiteralPath $androidSource -Destination $androidDestination -Recurse
    $properties = Join-Path $androidDestination 'build.local.properties'
    $storeFile = (Join-Path $androidDestination 'msdkkeystore.jks').Replace('\','\\')
    $propertyLines = @([IO.File]::ReadAllLines($properties) | ForEach-Object {
        if ($_ -match '^STORE_FILE=') { "STORE_FILE=$storeFile" } else { $_ }
    })
    [IO.File]::WriteAllLines($properties, [string[]]$propertyLines, $utf8)
}
Write-Output "Imported private settings to $Destination. No process or mission was started."
Write-Warning 'Use the matching installed APK and confirm current ground/RC state, phone IP, and site measurements before Real mode. Historic mission records are not resumed.'
