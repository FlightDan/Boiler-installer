# Shared by inventory and the last-moment deletion recheck. Missing metadata fails closed.
function Test-UnrecognizedPartitionAttributes($Disk, $Partition) {
    # Windows synthesizes IsHidden=true and IsReadOnly=null for non-Windows GPT
    # types. Read the actual GPT attributes; never clear them to allow deletion.
    if (-not ('OxpPartitionAttributes' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class OxpPartitionAttributes {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool DeviceIoControl(SafeFileHandle h, uint code, IntPtr input, uint inputSize, byte[] output, uint size, out uint returned, IntPtr overlap);
    static Guid Id(byte[] data, int offset) { var bytes=new byte[16]; Array.Copy(data,offset,bytes,0,16); return new Guid(bytes); }
    public static bool Unprotected(int disk, Guid diskId, Guid partId, Guid type, long offset, long length) {
        if(disk<0 || diskId==Guid.Empty || partId==Guid.Empty || type==Guid.Empty || offset<1048576 || length<=0) return false;
        using(var handle=CreateFile(@"\\.\PhysicalDrive"+disk,0x80000000,3,IntPtr.Zero,3,0,IntPtr.Zero)) {
            if(handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            var data=new byte[1024*1024]; uint returned;
            if(!DeviceIoControl(handle,0x70050,IntPtr.Zero,0,data,(uint)data.Length,out returned,IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if(returned<48 || returned>data.Length || BitConverter.ToUInt32(data,0)!=1 || Id(data,8)!=diskId) return false;
            var count=BitConverter.ToUInt32(data,4);
            if(count>(returned-48)/144) return false;
            int matches=0; bool clean=false;
            for(int i=0;i<count;i++) {
                int at=48+i*144;
                if(Id(data,at+48)!=partId) continue;
                matches++;
                clean=BitConverter.ToUInt32(data,at)==1 && Id(data,at+32)==type && BitConverter.ToInt64(data,at+8)==offset
                    && BitConverter.ToInt64(data,at+16)==length && BitConverter.ToUInt64(data,at+64)==0;
            }
            return matches==1 && clean;
        }
    }
}
'@
    }
    [OxpPartitionAttributes]::Unprotected([int]$Disk.Number,[guid]$Disk.Guid,[guid]$Partition.Guid,[guid]$Partition.GptType,[long]$Partition.Offset,[long]$Partition.Size)
}
function Get-PartitionDeletionBlockReason($Disk, $Partition, $Volume, $SteamOsEvidence = $null) {
    $Partition | Add-Member NoteProperty NativeUnrecognizedSafe $false -Force
    foreach ($name in @('IsOffline','IsReadOnly')) {
        if ($null -eq $Disk.$name -or $Disk.$name) { return 'Disk is offline, read-only, or protection metadata is unavailable.' }
    }
    if ([string]$Disk.PartitionStyle -ne 'GPT' -or [string]$Disk.BusType -notin @('NVMe','SATA','USB','SD','MMC') -or $Disk.LogicalSectorSize -ne 512) { return 'Unsupported disk layout.' }
    foreach ($name in @('IsBoot','IsSystem','IsOffline')) {
        if ($null -eq $Partition.$name -or $Partition.$name) { return 'System, boot, hidden, offline, or read-only partition is protected.' }
    }
    $type = [guid]::Empty
    if (-not [guid]::TryParse([string]$Partition.GptType, [ref]$type) -or $type -eq [guid]::Empty) { return 'Partition type cannot be identified.' }
    $steamBoot = $null -ne $SteamOsEvidence
    if ($steamBoot) {
        try {
            $role = @((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'steam-partition-roles.json') -Raw | ConvertFrom-Json) |
                Where-Object { $_.name -ceq $SteamOsEvidence.gptName -and [long]$_.bootSize -gt 0 })
            if ($role.Count -ne 1 -or $SteamOsEvidence.nativeGptVerified -ne $true -or [string]$SteamOsEvidence.gptAttributes -cne '0' -or
                [guid]$SteamOsEvidence.guid -ne [guid]$Partition.Guid -or [long]$SteamOsEvidence.offset -ne [long]$Partition.Offset -or
                [long]$SteamOsEvidence.size -ne [long]$Partition.Size -or [guid]$SteamOsEvidence.gptType -ne $type -or [guid]$role[0].gptType -ne $type) {
                return 'SteamOS boot partition ownership could not be verified.'
            }
            $native = Get-SteamOsDeletionMetadata $Disk $Partition
            if ($native.Id -ne [guid]$Partition.Guid -or $native.Type -ne $type -or $native.Offset -ne $Partition.Offset -or
                $native.Size -ne $Partition.Size -or $native.Name -cne $SteamOsEvidence.gptName -or [string]$native.Attributes -cne '0') {
                return 'SteamOS boot partition native identity or protection attributes changed.'
            }
            if ($Partition.IsReadOnly -eq $true) { return 'Hidden, read-only or unavailable partition protection metadata.' }
            $Partition.NativeUnrecognizedSafe = $true
        } catch { return 'SteamOS boot partition ownership could not be verified.' }
    }
    if ((-not $steamBoot -and ($type -eq [guid]'C12A7328-F81F-11D2-BA4B-00A0C93EC93B' -or [string]$Partition.Type -eq 'System')) -or
        $type -in @([guid]'E3C9E316-0B5C-4DB8-817D-F92DF00215AE', [guid]'DE94BBA4-06D1-4D40-A16A-BFD50179D6AC', [guid]'21686148-6449-6E6F-744E-656564454649') -or
        [string]$Partition.Type -in @('Reserved','Recovery')) { return 'EFI, MSR, recovery and boot partitions are protected.' }
    $unknown = $null -eq $Volume -or [string]$Volume.FileSystemType -in @('','Unknown','RAW')
    if (-not $steamBoot -and ($null -eq $Partition.IsHidden -or $Partition.IsHidden -or $null -eq $Partition.IsReadOnly -or $Partition.IsReadOnly)) {
        if (-not $unknown -or $type -eq [guid]'EBD0A0A2-B9E5-4433-87C0-68B6B72699C7' -or $Partition.IsReadOnly -eq $true) { return 'Hidden, read-only or unavailable partition protection metadata.' }
        try {
            if (-not (Test-UnrecognizedPartitionAttributes $Disk $Partition)) { return 'GPT protection attributes are set or unavailable.' }
            $Partition.NativeUnrecognizedSafe = $true
        } catch { return 'Native GPT protection attributes could not be verified.' }
    }
    if (-not $unknown -and ([string]$Volume.FileSystemType -notin @('NTFS','FAT32','exFAT','ReFS') -or [string]$Volume.HealthStatus -ne 'Healthy')) { return 'Volume is unhealthy or cannot be inspected.' }
    try {
        $paths = @($Partition.AccessPaths | Where-Object { $_ })
        $bootRoot = ''
        if ($steamBoot) {
            $bootRoot = Get-SteamOsBootRoot $Disk $Partition $Volume
            if (-not $bootRoot) { return 'SteamOS boot volume could not be inspected safely.' }
            $paths += $bootRoot
        }
        if (-not $unknown -and $paths.Count -eq 0) { return 'Volume has no verifiable access path.' }
        if ([string]$Partition.DriveLetter -match '^[A-Za-z]$') { $paths += ([string]$Partition.DriveLetter + ':\') }
        # Protect the installer, Windows, temp, pagefile and dump volumes, including folder mount points.
        $protectedPaths = @((Split-Path $PSScriptRoot -Parent), $env:SystemRoot, $env:TEMP, $env:TMP)
        $protectedPaths += @(Get-CimInstance Win32_PageFileUsage -ErrorAction Stop | ForEach-Object { $_.Name })
        $crash = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction Stop
        foreach ($key in @('DumpFile','MinidumpDir','DedicatedDumpFile')) {
            if ($crash.$key) { $protectedPaths += [Environment]::ExpandEnvironmentVariables([string]$crash.$key) }
        }
        foreach ($protectedPath in $protectedPaths) {
            if (-not $protectedPath) { continue }
            if ($steamBoot -and (Test-SteamOsBootHostPath $Disk $Partition $protectedPath)) {
                return 'This volume contains Windows, the installer, temporary files, a pagefile or crash dump.'
            }
            $full = [IO.Path]::GetFullPath($protectedPath).TrimEnd('\') + '\'
            foreach ($access in $paths) {
                if ($full.StartsWith($access, [StringComparison]::OrdinalIgnoreCase)) { return 'This volume contains Windows, the installer, temporary files, a pagefile or crash dump.' }
            }
        }
        # Unrecognized filesystems cannot be inspected for files. Metadata and protected
        # Windows paths are still checked; the UI requires explicit whole-partition consent.
        if ($steamBoot) {
            return Get-SteamOsBootContentBlockReason $bootRoot $SteamOsEvidence.gptName ($SteamOsEvidence.completeSteamOsLayout -ne $true)
        }
        if ($unknown) { return '' }
        # Also inspect the volume GUID path for recognized filesystems.
        $root = [string]$Volume.Path
        if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container -ErrorAction Stop)) { return 'Volume cannot be inspected safely.' }
        foreach ($name in @('Windows','Boot','EFI')) {
            if (Test-Path -LiteralPath (Join-Path $root $name) -ErrorAction Stop) { return 'Volume contains operating-system or boot files.' }
        }
    } catch { return 'Volume protection checks failed; refresh or unlock the volume first.' }
    return ''
}
