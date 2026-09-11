function Assert-DroneNonSyncedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    foreach ($syncRoot in @($env:OneDrive, $env:OneDriveCommercial, $env:OneDriveConsumer)) {
        if ($syncRoot) {
            $sync = [IO.Path]::GetFullPath($syncRoot).TrimEnd('\')
            if ($full.Equals($sync, [StringComparison]::OrdinalIgnoreCase) -or
                $full.StartsWith($sync + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Keep private drone settings outside OneDrive.'
            }
        }
    }
}

function Import-DroneLocalSettings {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-DroneNonSyncedPath -Path $Path
    $settings = @{}
    foreach ($line in [IO.File]::ReadAllLines((Resolve-Path -LiteralPath $Path).Path)) {
        $entry = $line.Trim()
        if (-not $entry -or $entry.StartsWith('#')) { continue }
        if ($entry -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw 'Settings must contain KEY=value lines.' }
        if ($settings.ContainsKey($Matches[1])) { throw 'Settings contain a duplicate key.' }
        $settings[$Matches[1]] = $Matches[2].Trim()
    }
    return $settings
}
