param([Parameter(Mandatory=$true)][string]$RequestPath, [string]$ExpectedDigest, [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
# Verify the bounded bytes from one open handle before JSON parsing or disk discovery.
if ($ExpectedDigest -cnotmatch '^[0-9a-f]{64}$') { throw 'Missing or invalid helper request digest.' }
$inputFile = [System.IO.File]::Open($RequestPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
try {
    $requestLength = $inputFile.Length
    if ($requestLength -le 0 -or $requestLength -gt 4MB) { throw 'Invalid helper request size.' }
    $requestBytes = New-Object byte[] ([int]$requestLength)
    $position = 0
    while ($position -lt $requestBytes.Length) {
        $count = $inputFile.Read($requestBytes, $position, $requestBytes.Length - $position)
        if ($count -le 0) { throw 'Truncated helper request.' }
        $position += $count
    }
    if ($inputFile.ReadByte() -ne -1) { throw 'Helper request grew during read.' }
} finally { $inputFile.Dispose() }
$prefix = [System.Text.Encoding]::UTF8.GetBytes("BoilerHelperRequest:v1:delete-partition`0")
$hash = [System.Security.Cryptography.SHA256]::Create()
try {
    [void]$hash.TransformBlock($prefix, 0, $prefix.Length, $prefix, 0)
    [void]$hash.TransformFinalBlock($requestBytes, 0, $requestBytes.Length)
    $actualDigest = [System.BitConverter]::ToString($hash.Hash).Replace('-', '').ToLowerInvariant()
} finally { $hash.Dispose() }
if ($actualDigest -cne $ExpectedDigest) { throw 'Helper request digest mismatch.' }
$strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$request = $strictUtf8.GetString($requestBytes) | ConvertFrom-Json
if ($request.Example -ne $false) { throw 'Example or incomplete requests cannot delete real partitions.' }
# Windows PowerShell 5.1 emits a root JSON array as one pipeline object.
# Enumerate the parsed array before selecting a disk, including disk number zero.
$expectedDisks = @(($request.Snapshot | ConvertFrom-Json))
$expected = @($expectedDisks | Where-Object { $_.number -eq $request.Disk })
if ($expected.Count -ne 1) { throw 'Ambiguous reviewed disk.' }
$expected = $expected[0]
$target = @($expected.partitions | Where-Object { $_.offset -eq $request.Start })
if ($target.Count -ne 1 -or $target[0].deletionAllowed -ne $true) { throw 'Selected partition was not eligible for deletion.' }
$target = $target[0]
$freshDisks = @(((& (Join-Path $PSScriptRoot 'read-disks.ps1')) | ConvertFrom-Json))
$fresh = @($freshDisks | Where-Object { $_.number -eq $request.Disk })
if ($fresh.Count -ne 1) { throw 'Disk is missing; refresh the disk list.' }
$fresh = $fresh[0]
foreach ($key in @('number','guid','serial','model','size','sectorSize','style','bus','isOffline','isReadOnly')) {
    if ($null -eq $expected.$key -or [string]$expected.$key -ne [string]$fresh.$key) { throw "Disk identity changed: $key. Refresh and select again." }
}
if ([guid]$fresh.guid -eq [guid]::Empty) { throw 'Disk GUID is missing.' }
if (@($expected.partitions).Count -ne @($fresh.partitions).Count) { throw 'Partition layout changed. Refresh and select again.' }
foreach ($part in $expected.partitions) {
    $same = @($fresh.partitions | Where-Object { $_.number -eq $part.number })
    if ($same.Count -ne 1) { throw 'Partition identity changed.' }
    foreach ($key in @('guid','offset','size','gptType','letter','label','filesystem','isBoot','isSystem','isHidden')) {
        if ($null -eq $part.$key -or [string]$part.$key -ne [string]$same[0].$key) { throw "Partition changed: $key. Refresh and select again." }
    }
    if ([guid]$part.guid -eq [guid]::Empty) { throw 'Partition GUID is missing.' }
}
$freshTarget = @($fresh.partitions | Where-Object { $_.guid -eq $target.guid })
if ($freshTarget.Count -ne 1 -or $freshTarget[0].deletionAllowed -ne $true) { throw 'Partition is now protected or cannot be inspected safely.' }
# Use the freshly acquired CIM object, never a wildcard or whole-disk operation.
$disk = Get-Disk -Number $request.Disk -ErrorAction Stop
$partition = Get-Partition -DiskNumber $request.Disk -PartitionNumber $target.number -ErrorAction Stop
if ([string]$disk.Guid -ne [string]$fresh.guid -or $disk.Size -ne $fresh.size -or [string]$partition.Guid -ne [string]$target.guid -or $partition.Offset -ne $target.offset -or $partition.Size -ne $target.size) { throw 'Device changed during the final check.' }
. (Join-Path $PSScriptRoot 'partition-delete-policy.ps1')
$volume = $partition | Get-Volume -ErrorAction SilentlyContinue
$reason = Get-PartitionDeletionBlockReason $disk $partition $volume
if ($reason) { throw $reason }
if ($ValidateOnly) { return }
$partition | Remove-Partition -Confirm:$false -ErrorAction Stop
$remaining = @(Get-Partition -DiskNumber $request.Disk -ErrorAction Stop)
if (@($remaining | Where-Object { [string]$_.Guid -eq [string]$target.guid }).Count -ne 0) { throw 'Deletion was not verified. Refresh the disk list before continuing.' }
if ($remaining.Count -ne @($expected.partitions).Count - 1) { throw 'Unexpected layout after deletion. Refresh and inspect the disk.' }
foreach ($part in @($expected.partitions | Where-Object { $_.guid -ne $target.guid })) {
    $same = @($remaining | Where-Object { [string]$_.Guid -eq [string]$part.guid -and $_.Offset -eq $part.offset -and $_.Size -eq $part.size -and [string]$_.GptType -eq [string]$part.gptType })
    if ($same.Count -ne 1) { throw 'Other partition metadata changed. Refresh and inspect the disk.' }
}
