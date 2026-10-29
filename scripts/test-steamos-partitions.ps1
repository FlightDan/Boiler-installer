param([Parameter(Mandatory=$true)][string]$FixturePath, [string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'steam-partition-layout.ps1')
. (Join-Path $PSScriptRoot 'partition-delete-snapshot.ps1')
$checks = New-Object 'System.Collections.Generic.List[string]'
function Assert-Check([bool]$Ok, [string]$Name) { if (-not $Ok) { throw ('Failed: ' + $Name) }; $checks.Add($Name) }
function Assert-Refused([scriptblock]$Action, [string]$Name) { $refused=$false; try { & $Action } catch { $refused=$true }; Assert-Check $refused $Name }
function Copy-Snapshot($Value) { $Value | ConvertTo-Json -Depth 10 | ConvertFrom-Json }
# Every environmental entry point is forbidden. Only mocked snapshots and byte
# buffers are used; compilation of P/Invoke declarations does not invoke them.
function Get-Disk { throw 'Real disk access forbidden in synthetic checks' }
function Get-Partition { throw 'Real partition access forbidden in synthetic checks' }
function Get-Volume { throw 'Real volume access forbidden in synthetic checks' }
function Remove-Partition { throw 'Real deletion forbidden in synthetic checks' }
$disks = @((Get-Content -LiteralPath $FixturePath -Raw | ConvertFrom-Json))
$expected = $disks[0]
$expected | Add-Member NoteProperty serial 'synthetic-steamos-reinstall' -Force
$recognized = @(Get-SteamOsPartitionStarts $expected)
Assert-Check ($recognized.Count -eq 8) 'recognizes-complete-layout-with-protected-esp'
$targets = @($expected.partitions | Where-Object { $recognized -contains [long]$_.offset -and $_.deletionAllowed })
Assert-Check ($targets.Count -eq 5) 'only-existing-policy-eligible-partitions-selected'
foreach ($key in @('gptName','gptType','nativeGptVerified','missing','gap','label-only')) {
    $changed = Copy-Snapshot $expected
    $part = @($changed.partitions | Where-Object { $_.gptName -eq 'rootfs-A' })[0]
    switch ($key) {
        'gptName' { $part.gptName='rootfs-a' }
        'gptType' { $part.gptType='0fc63daf-8483-4772-8e79-3d69d8477de4' }
        'nativeGptVerified' { $part.nativeGptVerified=$false }
        'missing' { $changed.partitions=@($changed.partitions | Where-Object { $_.guid -ne $part.guid }) }
        'gap' { $part.size-=1MB }
        'label-only' { foreach ($p in $changed.partitions) { $p.label=$p.gptName; $p.gptName='' } }
    }
    Assert-Check (@(Get-SteamOsPartitionStarts $changed).Count -eq 0) ('rejects-incomplete-or-unverified:'+$key)
}
foreach ($key in @('guid','size','gptName','gptAttributes','nativeGptVerified','isSystem','filesystem')) {
    $changed=Copy-Snapshot $expected
    switch ($key) {
        'guid' { $changed.guid=[guid]::NewGuid().ToString() }
        'size' { $changed.partitions[0].size+=1MB }
        'gptName' { $changed.partitions[0].gptName='changed' }
        'gptAttributes' { $changed.partitions[0].gptAttributes='1' }
        'nativeGptVerified' { $changed.partitions[0].nativeGptVerified=$false }
        'isSystem' { $changed.partitions[0].isSystem=-not $changed.partitions[0].isSystem }
        'filesystem' { $changed.partitions[0].filesystem='Unknown' }
    }
    Assert-Refused { Assert-DeletionSnapshot $expected $changed @($expected.partitions) $true } ('rebind-rejects:'+ $key)
}
$renumbered=Copy-Snapshot $expected
foreach ($p in $renumbered.partitions) { $p.number+=20 }
Assert-DeletionSnapshot $expected $renumbered @($expected.partitions) $true
Assert-Check $true 'stable-guid-binding-allows-windows-renumbering'
Initialize-SteamPartitionReader
$native = New-Object byte[] (48 + 144 * @($expected.partitions).Count)
function Put-Bytes([int]$At, [byte[]]$Bytes) { [Array]::Copy($Bytes,0,$native,$At,$Bytes.Length) }
Put-Bytes 0 ([BitConverter]::GetBytes([uint32]1)); Put-Bytes 4 ([BitConverter]::GetBytes([uint32]@($expected.partitions).Count))
Put-Bytes 8 (([guid]$expected.guid).ToByteArray())
$index=0
foreach ($p in $expected.partitions) {
    $at=48+144*$index; $index++
    Put-Bytes $at ([BitConverter]::GetBytes([uint32]1))
    Put-Bytes ($at+8) ([BitConverter]::GetBytes([long]$p.offset)); Put-Bytes ($at+16) ([BitConverter]::GetBytes([long]$p.size))
    Put-Bytes ($at+24) ([BitConverter]::GetBytes([uint32]$p.number)); Put-Bytes ($at+32) (([guid]$p.gptType).ToByteArray())
    Put-Bytes ($at+48) (([guid]$p.guid).ToByteArray()); Put-Bytes ($at+72) ([Text.Encoding]::Unicode.GetBytes([string]$p.gptName))
}
$parsed=@([BoilerSteamPartitionReader]::Parse($native,$native.Length,[guid]$expected.guid,[long]$expected.size))
Assert-Check ($parsed.Count -eq @($expected.partitions).Count -and @($parsed | Where-Object { $_.Name -eq 'home' }).Count -eq 1) 'native-pure-parser-recovers-role-names'
Assert-Refused { [void][BoilerSteamPartitionReader]::Parse($native,$native.Length,[guid]::NewGuid(),[long]$expected.size) } 'native-rejects-swapped-disk'
Assert-Refused { [void][BoilerSteamPartitionReader]::Parse($native,47,[guid]$expected.guid,[long]$expected.size) } 'native-rejects-short-response'
Put-Bytes 4 ([BitConverter]::GetBytes([uint32]999999))
Assert-Refused { [void][BoilerSteamPartitionReader]::Parse($native,$native.Length,[guid]$expected.guid,[long]$expected.size) } 'native-rejects-out-of-bounds-count'
# Exercise the actual sequential orchestration with mock disk/target functions.
function Reset-Mock {
    $script:current=Copy-Snapshot $expected; $script:deleted=@(); $script:preflights=0; $script:stopAt=0; $script:changeAfter=$false
}
function Read-ReviewedDeletionDisk([int]$Number) { Copy-Snapshot $script:current }
function Get-ReviewedDeletionTarget([int]$Number,$Fresh,$Target) { $script:preflights++; return $Target }
function Remove-Partition {
    [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Confirm)
    process {
        if ($script:stopAt -gt 0 -and $script:deleted.Count -eq $script:stopAt) { throw 'Synthetic removal failure' }
        $script:deleted+=@($InputObject.guid)
        $script:current.partitions=@($script:current.partitions | Where-Object { $_.guid -ne $InputObject.guid })
        if ($script:changeAfter) { $script:current.partitions[0].gptName='concurrent-change' }
    }
}
Reset-Mock
Invoke-ReviewedPartitionDeletion $expected $script:current $targets $true -ValidateOnly
Assert-Check ($script:deleted.Count -eq 0 -and $script:preflights -eq 5) 'preflight-validates-every-target-with-zero-removals'
Reset-Mock
Invoke-ReviewedPartitionDeletion $expected $script:current $targets $true
Assert-Check ($script:deleted.Count -eq 5 -and $script:preflights -eq 10) 'batch-rechecks-every-target-before-each-removal'
Assert-Check (@($script:current.partitions | Where-Object { $_.gptName -in @('esp','efi-A','efi-B') }).Count -eq 3) 'batch-keeps-protected-boot-partitions'
Assert-Check (@($script:current.partitions | Where-Object { $_.filesystem -eq 'NTFS' }).Count -eq 3) 'batch-keeps-windows-data-and-recovery'
Reset-Mock; $script:stopAt=1
Assert-Refused { Invoke-ReviewedPartitionDeletion $expected $script:current $targets $true } 'partial-removal-failure-stops-batch'
Assert-Check ($script:deleted.Count -eq 1) 'partial-removal-has-no-retry-or-further-deletions'
Reset-Mock; $script:changeAfter=$true
Assert-Refused { Invoke-ReviewedPartitionDeletion $expected $script:current $targets $true } 'unselected-layout-change-stops-batch'
Assert-Check ($script:deleted.Count -eq 1) 'unselected-change-stops-before-second-removal'
if (-not $ReportDirectory) { $ReportDirectory=Join-Path (Split-Path $PSScriptRoot -Parent) ('work/steamos-partition-policy-'+[guid]::NewGuid().ToString('N')) }
$null=New-Item -ItemType Directory -Path $ReportDirectory -Force
$path=Join-Path $ReportDirectory 'checks.json'
[ordered]@{passed=$true;checks=@($checks.ToArray());scope='Pure snapshot/native-byte parsers and mocked sequential deletion. All real disk queries/removals forbidden.'} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding UTF8
$path
