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
. (Join-Path $PSScriptRoot 'partition-delete-policy.ps1')
. (Join-Path $PSScriptRoot 'steam-partition-layout.ps1')
. (Join-Path $PSScriptRoot 'partition-delete-snapshot.ps1')
$batch = $null -ne $request.SteamOsStarts
$starts = @($request.Start)
if ($batch) { $starts = @($request.SteamOsStarts) }
if ($starts.Count -lt 1 -or $starts.Count -gt 128 -or @($starts | Select-Object -Unique).Count -ne $starts.Count -or
    ($starts | Measure-Object -Minimum).Minimum -ne $request.Start) { throw 'Invalid deletion selection.' }
$targets = @($starts | ForEach-Object {
    $start = $_
    $match = @($expected.partitions | Where-Object { $_.offset -eq $start })
    if ($match.Count -ne 1 -or $match[0].deletionAllowed -ne $true) { throw 'Selected partition was not eligible for deletion.' }
    $match[0]
})
$fresh = Read-ReviewedDeletionDisk $request.Disk
Assert-DeletionSnapshot $expected $fresh @($expected.partitions) $batch
if ($batch) {
    $recognized = @(Get-SteamOsPartitionStarts $fresh)
    foreach ($target in $targets) { if ($recognized -notcontains [long]$target.offset) { throw 'Selected partition does not belong to a complete SteamOS layout.' } }
}
# Preflight every selected partition before the first mutation. The elevated app
# also runs this pass before showing its mandatory confirmation dialog.
Invoke-ReviewedPartitionDeletion $expected $fresh $targets $batch -ValidateOnly:$ValidateOnly
