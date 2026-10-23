param([string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if (-not $ReportDirectory) { $ReportDirectory = Join-Path $taskRoot ('work/script-dependency-checks-' + [guid]::NewGuid().ToString('N')) }
$events = New-Object 'System.Collections.Generic.List[string]'
function Assert-Check([bool]$Condition, [string]$Name) {
    if (-not $Condition) { throw ('Check failed: ' + $Name) }
    $events.Add($Name)
}
$whitelist = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'source-files.json') -Raw | ConvertFrom-Json).files
# Parse every bundled PS source without invoking its top-level commands.
foreach ($script in @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1')) {
    $tokens = $null; $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$errors)
    Assert-Check ($errors.Count -eq 0) ($script.Name + ':powershell-parser-valid')
    Assert-Check ($whitelist -contains ('scripts/' + $script.Name)) ($script.Name + ':source-whitelisted')
    $text = Get-Content -LiteralPath $script.FullName -Raw
    foreach ($match in [regex]::Matches($text, 'Join-Path\s+\$PSScriptRoot\s+[''"]([^''"]+\.(?:ps1|py|json))[''"]')) {
        $relative = $match.Groups[1].Value
        Assert-Check (Test-Path -LiteralPath (Join-Path $PSScriptRoot $relative) -PathType Leaf) ($script.Name + ':bundled-reference-present:' + $relative)
        Assert-Check ($whitelist -contains ('scripts/' + $relative)) ($script.Name + ':bundled-reference-whitelisted:' + $relative)
    }
}
# The application only dispatches a single bundled script basename through
# Backend.PowerShellAsync. Check every literal dispatch in all application sources.
foreach ($source in @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'app') -Filter '*.cs')) {
    $text = Get-Content -LiteralPath $source.FullName -Raw
    foreach ($match in [regex]::Matches($text, 'Backend\.PowerShellAsync\(\s*"([^"\r\n]+\.ps1)"')) {
        $relative = $match.Groups[1].Value
        Assert-Check (Test-Path -LiteralPath (Join-Path $PSScriptRoot $relative) -PathType Leaf) ($source.Name + ':backend-script-present:' + $relative)
        Assert-Check ($whitelist -contains ('scripts/' + $relative)) ($source.Name + ':backend-script-whitelisted:' + $relative)
    }
}
. (Join-Path $PSScriptRoot 'partition-delete-policy.ps1')
. (Join-Path $PSScriptRoot 'linux-storage-probe.ps1')
foreach ($name in @('Get-PartitionDeletionBlockReason', 'Test-UnrecognizedPartitionAttributes', 'Test-LinuxStorageProbeEligible', 'Get-LinuxStorageFilesystem', 'Initialize-LinuxStorageProbe')) {
    Assert-Check ($null -ne (Get-Command $name -CommandType Function -ErrorAction SilentlyContinue)) ('function-loads:' + $name)
}
# These synthetic objects are rejected before any CIM/registry/native access.
# Block every environmental query used by policy; an accidental call fails the
# check instead of reading or modifying a real disk or host configuration.
$script:environmentalCalls = 0
function Get-CimInstance { $script:environmentalCalls++; throw 'Unexpected CIM access in synthetic policy check' }
function Get-ItemProperty { $script:environmentalCalls++; throw 'Unexpected registry access in synthetic policy check' }
function Test-UnrecognizedPartitionAttributes { $script:environmentalCalls++; throw 'Unexpected native access in synthetic policy check' }
function Get-Disk { $script:environmentalCalls++; throw 'Unexpected disk query in synthetic policy check' }
function Get-Partition { $script:environmentalCalls++; throw 'Unexpected partition query in synthetic policy check' }
function Remove-Partition { throw 'Deletion is forbidden in dependency checks' }
function Format-Volume { throw 'Formatting is forbidden in dependency checks' }
function New-SyntheticDisk {
    [pscustomobject]@{Number=-1; Guid=[guid]::NewGuid(); IsOffline=$false; IsReadOnly=$false; PartitionStyle='GPT'; BusType='USB'; LogicalSectorSize=512}
}
function New-SyntheticPartition {
    [pscustomobject]@{Guid=[guid]::NewGuid(); GptType='EBD0A0A2-B9E5-4433-87C0-68B6B72699C7'; Type='Basic'; IsBoot=$false; IsSystem=$false; IsOffline=$false; IsHidden=$false; IsReadOnly=$false; AccessPaths=@(); Offset=1048576; Size=67108864}
}
$cases = @('missing-disk-offline','offline-disk','readonly-disk','unsupported-style','unsupported-bus','unsupported-sector','boot-partition','system-partition','offline-partition','unknown-type','efi','msr','recovery','bios-boot','hidden-basic','readonly-partition','unhealthy-volume')
foreach ($case in $cases) {
    $disk = New-SyntheticDisk; $part = New-SyntheticPartition
    $volume = [pscustomobject]@{FileSystemType='NTFS'; HealthStatus='Healthy'}
    switch ($case) {
        'missing-disk-offline' { $disk.IsOffline=$null }
        'offline-disk' { $disk.IsOffline=$true }
        'readonly-disk' { $disk.IsReadOnly=$true }
        'unsupported-style' { $disk.PartitionStyle='MBR' }
        'unsupported-bus' { $disk.BusType='Unknown' }
        'unsupported-sector' { $disk.LogicalSectorSize=4096 }
        'boot-partition' { $part.IsBoot=$true }
        'system-partition' { $part.IsSystem=$true }
        'offline-partition' { $part.IsOffline=$true }
        'unknown-type' { $part.GptType='invalid' }
        'efi' { $part.GptType='C12A7328-F81F-11D2-BA4B-00A0C93EC93B' }
        'msr' { $part.GptType='E3C9E316-0B5C-4DB8-817D-F92DF00215AE' }
        'recovery' { $part.GptType='DE94BBA4-06D1-4D40-A16A-BFD50179D6AC' }
        'bios-boot' { $part.GptType='21686148-6449-6E6F-744E-656564454649' }
        'hidden-basic' { $part.IsHidden=$true }
        'readonly-partition' { $part.IsReadOnly=$true }
        'unhealthy-volume' { $volume.HealthStatus='Unhealthy' }
    }
    $reason = Get-PartitionDeletionBlockReason $disk $part $volume
    Assert-Check (-not [string]::IsNullOrEmpty($reason)) ('synthetic-deletion-refused:' + $case)
    Assert-Check ($part.NativeUnrecognizedSafe -eq $false) ('synthetic-native-safety-not-granted:' + $case)
}
Assert-Check ($script:environmentalCalls -eq 0) 'synthetic-policy-refusals-without-cim-registry-native-or-disk-queries'
$disk = New-SyntheticDisk; $part = New-SyntheticPartition
$disk.PartitionStyle = 'MBR'
Assert-Check (-not (Test-LinuxStorageProbeEligible $disk $part $null)) 'synthetic-linux-probe-unsupported-style-refused-without-device-access'
$disk.PartitionStyle = 'GPT'; $disk.IsReadOnly = $true
Assert-Check (-not (Test-LinuxStorageProbeEligible $disk $part $null)) 'synthetic-linux-probe-readonly-refused-without-device-access'
# Compilation only defines native declarations. Invoke only the pure byte parser
# and layout parser, never Probe (which would open a disk).
Initialize-LinuxStorageProbe
Assert-Check ([OxpLinuxStorageProbe]::Identify((New-Object byte[] 131072), 131072) -eq '') 'linux-pure-byte-parser-rejects-blank-buffer'
Assert-Check (-not [OxpLinuxStorageProbe]::MatchesLayout((New-Object byte[] 48), 48, [guid]::NewGuid(), [guid]::NewGuid(), [guid]::NewGuid(), 1048576, 67108864)) 'linux-pure-layout-parser-rejects-invalid-layout'
$hashes = @()
foreach ($name in @('partition-delete-policy.ps1', 'linux-storage-probe.ps1', 'read-usb-media.ps1', 'vhd-test-volumes.ps1')) {
    $hashes += [ordered]@{file=('scripts/' + $name);sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$null = New-Item -ItemType Directory -Path $ReportDirectory -Force
$report = [ordered]@{passed=$true;events=@($events.ToArray());dependencies=$hashes;scope='PowerShell syntax, explicit bundled script reference/whitelist checks, function loading, synthetic policy refusals and pure byte parsers only. No disk enumeration, native device probes, formatting, deletion, app CLI, or administrator operations.'}
$reportPath = Join-Path $ReportDirectory 'checks.json'
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Output $reportPath
