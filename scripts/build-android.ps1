param(
    [Parameter(Mandatory = $true)][string]$PrivateProperties,
    [string]$SourceRoot = '',
    [string]$BuildRoot = (Join-Path $env:LOCALAPPDATA ('IndustryDayDrone\android-build\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    [string]$AndroidSdk = (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'load-local-settings.ps1')
$revision = 'a48aa4e7811d824c27abfa973f5655579bfb8a77'
foreach ($path in @($PrivateProperties, $BuildRoot)) {
    Assert-DroneNonSyncedPath -Path $path
    $full = [IO.Path]::GetFullPath($path)
    if ($full.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Keep private properties and Android build output outside this repository.'
    }
}
if (-not (Test-Path -LiteralPath $PrivateProperties -PathType Leaf)) { throw 'Private Gradle properties file is missing.' }
if (-not (Test-Path -LiteralPath $AndroidSdk -PathType Container)) { throw 'Install Android SDK and accept its licenses first.' }
if (Test-Path -LiteralPath $BuildRoot) { throw 'Choose a new BuildRoot; existing build directories are not overwritten.' }
$null = Get-Command git, java -ErrorAction Stop
New-Item -ItemType Directory -Path $BuildRoot | Out-Null
if (-not $SourceRoot) {
    $SourceRoot = Join-Path $BuildRoot 'upstream'
    git clone --quiet --no-checkout https://github.com/dji-sdk/Mobile-SDK-Android-V5.git $SourceRoot
    if ($LASTEXITCODE -ne 0) { throw 'DJI sample clone failed.' }
    git -C $SourceRoot checkout --quiet --detach $revision
    if ($LASTEXITCODE -ne 0) { throw 'Pinned DJI sample checkout failed.' }
}
$head = git -C $SourceRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $head -ne $revision) { throw 'SourceRoot must contain the pinned DJI sample revision.' }
$dirty = git -C $SourceRoot status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Use an unchanged upstream source checkout; private settings are supplied separately.' }
$sample = Join-Path $BuildRoot 'SampleCode-V5'
& robocopy.exe (Join-Path $SourceRoot 'SampleCode-V5') $sample /E /XD .gradle build /XF local.properties /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -gt 7) { throw 'Copying the full DJI sample failed.' }
& robocopy.exe (Join-Path $root 'drone-control\android\SampleCode-V5') $sample /E /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -gt 7) { throw 'Applying the repository Android overlay failed.' }
$saved = @{}
foreach ($key in @('ANDROID_HOME', 'ANDROID_SDK_ROOT', 'DRONE_ANDROID_PROPERTIES')) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
Push-Location (Join-Path $sample 'android-sdk-v5-as')
try {
    $env:ANDROID_HOME = $AndroidSdk
    $env:ANDROID_SDK_ROOT = $AndroidSdk
    $env:DRONE_ANDROID_PROPERTIES = (Resolve-Path -LiteralPath $PrivateProperties).Path
    & .\gradlew.bat --no-daemon --console=plain -I (Join-Path $PSScriptRoot 'android-private.init.gradle') `
        :bridge:testDebugUnitTest :uxsdk:testDebugUnitTest :sample:assembleDebug
    if ($LASTEXITCODE -ne 0) { throw 'Android build failed. The existing phone app was not changed.' }
    $apk = Join-Path $sample 'android-sdk-v5-sample\build\outputs\apk\debug\sample-debug.apk'
    if (-not (Test-Path -LiteralPath $apk -PathType Leaf)) { throw 'Expected APK output is missing.' }
    Get-FileHash -LiteralPath $apk -Algorithm SHA256
    Write-Output "APK=$apk"
    Write-Output 'No installation or flight was performed. An update requires the same signing certificate as the installed app.'
} finally {
    Pop-Location
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}
