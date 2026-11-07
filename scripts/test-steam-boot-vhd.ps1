param([Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'steam-boot-inspection.ps1')
# Only the newly created, randomly named VHD below may be initialized/formatted.
# Production deletion is NOT invoked; its bus policy rejects file-backed VHDs.
if (-not [IO.Path]::IsPathRooted($ReportDirectory)) { throw 'Absolute dedicated RAID report directory required.' }
$parent=Get-Item -LiteralPath $ReportDirectory -ErrorAction Stop
if (-not $parent.PSIsContainer -or ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Ordinary report directory required.' }
$drive=[IO.Path]::GetPathRoot($parent.FullName).TrimEnd(':\')
$storage=Get-Volume -DriveLetter $drive -ErrorAction Stop
if ($drive -eq 'C' -or $storage.HealthStatus -ne 'Healthy' -or $storage.SizeRemaining -lt 2GB) { throw 'Healthy non-C workspace with 2 GiB free required.' }
$run=Join-Path $parent.FullName ('native-vhd-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $run
$path=Join-Path $run 'media.vhdx'
$checks=New-Object 'System.Collections.Generic.List[string]'
$attached=$false
function Assert-Check([bool]$Ok,[string]$Name) { if (-not $Ok) { throw ('Failed: '+$Name) }; $checks.Add($Name) }
function Get-OwnedDisk {
    $image=Get-DiskImage -ImagePath $path -ErrorAction Stop
    if (-not $image.Attached -or $image.ImagePath -ine $path) { throw 'Owned VHD mapping changed.' }
    $mapped=@($image | Get-Disk -ErrorAction Stop)
    if ($mapped.Count -ne 1 -or [string]$mapped[0].BusType -notin @('File Backed Virtual','FileBackedVirtual') -or $mapped[0].Size -ne 768MB -or $mapped[0].IsBoot -or $mapped[0].IsSystem -or $mapped[0].IsReadOnly -or $mapped[0].IsOffline) { throw 'Not the new, writable file-backed fixture.' }
    return $mapped[0]
}
try {
    $created=New-VHD -Path $path -SizeBytes 768MB -Dynamic -ErrorAction Stop
    $null=Mount-VHD -Path $path -ErrorAction Stop; $attached=$true
    $disk=Get-OwnedDisk
    if ($disk.PartitionStyle -ne 'RAW') { throw 'New VHD is not blank.' }
    $null=Initialize-Disk -InputObject $disk -PartitionStyle GPT -ErrorAction Stop
    $parts=@()
    foreach ($size in @(256MB,64MB,64MB)) {
        $disk=Get-OwnedDisk
        $part=New-Partition -DiskNumber $disk.Number -Size $size -ErrorAction Stop
        if ($part.IsBoot -or $part.IsSystem -or $part.DiskNumber -ne $disk.Number -or $part.Size -ne $size) { throw 'Unexpected fixture partition.' }
        $null=Format-Volume -Partition $part -FileSystem FAT32 -NewFileSystemLabel 'BOILER_LAB' -Force -Confirm:$false -ErrorAction Stop
        $parts+=@($part)
    }
    for ($i=0;$i -lt 3;$i++) {
        $disk=Get-OwnedDisk
        $part=Get-Partition -DiskNumber $disk.Number -PartitionNumber $parts[$i].PartitionNumber -ErrorAction Stop
        $volume=$part | Get-Volume -ErrorAction Stop
        if ($volume.FileSystemLabel -ne 'BOILER_LAB') { throw 'Foreign fixture volume.' }
        $root=Get-SteamOsBootRoot $disk $part $volume
        $role=@('esp','efi-A','efi-B')[$i]
        # Write tiny synthetic content only to this bound test volume.
        if ($i -eq 0) {
            $null=[IO.Directory]::CreateDirectory($root+'SteamOS\conf')
            [IO.File]::WriteAllText($root+'SteamOS\conf\synthetic.conf','title SteamOS')
        } else {
            $null=[IO.Directory]::CreateDirectory($root+'EFI\steamos')
            [IO.File]::WriteAllText($root+'EFI\steamos\grub.cfg',"steamenv_boot /boot/vmlinuz-linux`n")
        }
        $reason=Get-SteamOsBootContentBlockReason $root $role
        Assert-Check ($reason -eq '') ($role+'-native-guid-provider-content-readable: '+$reason)
        Assert-Check (-not (Test-SteamOsBootHostPath $disk $part $PSScriptRoot)) ($role+'-host-directory-excluded-by-handle')
        Assert-Check ((Get-SteamOsBootRoot $disk $part $null) -eq $root) ($role+'-no-volume-path-native-discovery')
    }
    # Verify the real EFI type with no drive-letter assignment too.
    $disk=Get-OwnedDisk
    $first=Get-Partition -DiskNumber $disk.Number -PartitionNumber $parts[0].PartitionNumber -ErrorAction Stop
    $null=Set-Partition -InputObject $first -GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -ErrorAction Stop
    $first=Get-Partition -DiskNumber $disk.Number -PartitionNumber $parts[0].PartitionNumber -ErrorAction Stop
    Assert-Check (-not $first.DriveLetter -and -not $first.IsBoot -and -not $first.IsSystem) 'fixture-esp-has-no-drive-letter-or-host-use'
    $root=Get-SteamOsBootRoot $disk $first $null
    Assert-Check ((Get-SteamOsBootContentBlockReason $root 'esp') -eq '') 'efi-type-native-guid-content-readable'
    [ordered]@{passed=$true;checks=$checks.ToArray();scope='Native volume extent/host binding and Windows PowerShell GUID-root content inspection on a newly created owned VHD only. Not production deletion, VM, physical hardware, or installer acceptance.'} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $run 'checks.json') -Encoding UTF8
    Join-Path $run 'checks.json'
} catch {
    [ordered]@{passed=$false;checks=$checks.ToArray();error=$_.Exception.ToString();scope='Owned VHD native read integration; no production deletion.'} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $run 'blocked.json') -Encoding UTF8
    throw
} finally {
    if ($attached) { Dismount-VHD -Path $path -ErrorAction Stop }
    # Preserve the image and reports for inspection. No file cleanup/deletion.
}
