# No top-level I/O. These helpers keep the whole unselected layout bound across
# sequential deletion. Any uncertainty stops the batch; there is no automatic retry.
function Read-ReviewedDeletionDisk([int]$Number) {
    $disks = @(((& (Join-Path $PSScriptRoot 'read-disks.ps1')) | ConvertFrom-Json))
    $match = @($disks | Where-Object { $_.number -eq $Number })
    if ($match.Count -ne 1 -or $match[0].partitionReadError) { throw 'Disk inventory is missing or incomplete. Refresh disks.' }
    return $match[0]
}
function Assert-DeletionSnapshot($Expected, $Fresh, $Remaining, [bool]$NativeNames) {
    foreach ($key in @('number','guid','serial','model','size','sectorSize','style','bus','isOffline','isReadOnly')) {
        if ($null -eq $Expected.$key -or [string]$Expected.$key -cne [string]$Fresh.$key) { throw "Disk identity changed: $key. Refresh and select again." }
    }
    if ([guid]$Fresh.guid -eq [guid]::Empty -or $Fresh.partitionReadError) { throw 'Invalid disk identity or incomplete layout.' }
    if (@($Remaining).Count -ne @($Fresh.partitions).Count) { throw 'Partition layout changed. Refresh and inspect the disk.' }
    foreach ($part in $Remaining) {
        # Windows can renumber after removal. GUID, type and exact range remain authoritative.
        $same = @($Fresh.partitions | Where-Object { $_.guid -eq $part.guid })
        if ($same.Count -ne 1) { throw 'Partition identity changed.' }
        $keys = @('guid','offset','size','gptType','letter','label','filesystem','isBoot','isSystem','isHidden')
        if ($NativeNames) { $keys += @('gptName','gptAttributes','nativeGptVerified') }
        foreach ($key in $keys) {
            if ($null -eq $part.$key -or [string]$part.$key -cne [string]$same[0].$key) { throw "Partition changed: $key. Refresh and select again." }
        }
        if ([guid]$part.guid -eq [guid]::Empty) { throw 'Partition GUID is missing.' }
    }
}
function Get-ReviewedDeletionTarget([int]$Number, $Fresh, $Target, $SteamOsEvidence = $null) {
    $same = @($Fresh.partitions | Where-Object { $_.guid -eq $Target.guid })
    # During a reviewed batch the original group is intentionally dismantled.
    # Recompute boot eligibility with its bound context, never with a stale allow flag.
    if ($same.Count -ne 1 -or ($null -eq $SteamOsEvidence -and $same[0].deletionAllowed -ne $true)) { throw 'Partition is now protected or cannot be inspected safely.' }
    $disk = Get-Disk -Number $Number -ErrorAction Stop
    $partition = Get-Partition -DiskNumber $Number -PartitionNumber $same[0].number -ErrorAction Stop
    if ([string]$disk.Guid -ne [string]$Fresh.guid -or $disk.Size -ne $Fresh.size -or [string]$partition.Guid -ne [string]$Target.guid -or
        $partition.Offset -ne $Target.offset -or $partition.Size -ne $Target.size -or [guid]$partition.GptType -ne [guid]$Target.gptType) { throw 'Device changed during the final check.' }
    $volume = $partition | Get-Volume -ErrorAction SilentlyContinue
    $reason = Get-PartitionDeletionBlockReason $disk $partition $volume $SteamOsEvidence
    if ($reason) { throw $reason }
    return $partition
}
function Invoke-ReviewedPartitionDeletion($Expected, $Fresh, $Targets, [bool]$NativeNames, [switch]$ValidateOnly) {
    # This context comes from a freshly rebound complete group or verified
    # residual candidate, not from a caller-supplied role assertion.
    $context = Get-SteamOsDeletionContext $Fresh
    $bootContext = @{}
    foreach ($target in $Targets) {
        $same = @($Fresh.partitions | Where-Object { $_.guid -eq $target.guid })
        if ($same.Count -ne 1 -or $same[0].deletionAllowed -ne $true) { throw 'Selected partition is now protected.' }
        $key=([guid]$target.guid).ToString('D')
        $evidence=$context[$key]
        if ($null -ne $evidence -and $evidence.gptName -cin @('esp','efi-A','efi-B')) { $bootContext[$key]=$evidence; $NativeNames=$true }
    }
    Assert-DeletionSnapshot $Expected $Fresh @($Expected.partitions) $NativeNames
    foreach ($target in $Targets) { $null = Get-ReviewedDeletionTarget $Expected.number $Fresh $target $bootContext[([guid]$target.guid).ToString('D')] }
    if ($ValidateOnly) { return }
    $remaining = @($Expected.partitions)
    foreach ($target in @($Targets | Sort-Object offset -Descending)) {
        $fresh = Read-ReviewedDeletionDisk $Expected.number
        Assert-DeletionSnapshot $Expected $fresh $remaining $NativeNames
        $partition = Get-ReviewedDeletionTarget $Expected.number $fresh $target $bootContext[([guid]$target.guid).ToString('D')]
        $partition | Remove-Partition -Confirm:$false -ErrorAction Stop
        $remaining = @($remaining | Where-Object { $_.guid -ne $target.guid })
        $after = Read-ReviewedDeletionDisk $Expected.number
        Assert-DeletionSnapshot $Expected $after $remaining $NativeNames
    }
}
