# Read-only GPT names/attributes, bound to the complete CIM partition inventory.
# Volume labels and arbitrary Linux filesystems are not SteamOS ownership evidence.
function Initialize-SteamPartitionReader {
    if ('BoilerSteamPartitionReader' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public sealed class BoilerSteamPartitionMetadata {
    public int Number; public Guid Id; public Guid Type;
    public long Offset; public long Size; public string Name; public string Attributes;
}
public static class BoilerSteamPartitionReader {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool DeviceIoControl(SafeFileHandle handle, uint code, IntPtr input, uint inputSize, byte[] output, uint size, out uint returned, IntPtr overlap);
    static Guid Id(byte[] data, int offset) { var bytes=new byte[16]; Array.Copy(data,offset,bytes,0,16); return new Guid(bytes); }
    public static BoilerSteamPartitionMetadata[] Parse(byte[] data, uint returned, Guid diskId, long capacity) {
        if (returned<48 || returned>data.Length || BitConverter.ToUInt32(data,0)!=1 || diskId==Guid.Empty || Id(data,8)!=diskId || capacity<=0)
            throw new InvalidOperationException("Native GPT disk binding failed.");
        uint count=BitConverter.ToUInt32(data,4);
        if (count>(returned-48)/144) throw new InvalidOperationException("Incomplete native GPT metadata.");
        var result=new List<BoilerSteamPartitionMetadata>(); var ids=new HashSet<Guid>(); var numbers=new HashSet<int>();
        for (int i=0;i<count;i++) {
            int at=48+i*144; long offset=BitConverter.ToInt64(data,at+8), size=BitConverter.ToInt64(data,at+16);
            if (size==0) continue;
            uint number=BitConverter.ToUInt32(data,at+24); Guid id=Id(data,at+48), type=Id(data,at+32);
            if (BitConverter.ToUInt32(data,at)!=1 || number==0 || number>int.MaxValue || offset<1048576 || size<0 || offset>capacity-size
                || offset%512!=0 || size%512!=0 || id==Guid.Empty || type==Guid.Empty || !ids.Add(id) || !numbers.Add((int)number))
                throw new InvalidOperationException("Invalid native GPT partition identity/range.");
            string name=Encoding.Unicode.GetString(data,at+72,72).TrimEnd('\0');
            if (name.IndexOf('\0')>=0) throw new InvalidOperationException("Malformed GPT name.");
            result.Add(new BoilerSteamPartitionMetadata { Number=(int)number, Id=id, Type=type, Offset=offset, Size=size, Name=name,
                Attributes=BitConverter.ToUInt64(data,at+64).ToString(System.Globalization.CultureInfo.InvariantCulture) });
        }
        result.Sort((a,b)=>a.Offset.CompareTo(b.Offset)); long end=0;
        foreach (var part in result) { if (part.Offset<end) throw new InvalidOperationException("Overlapping native GPT ranges."); end=part.Offset+part.Size; }
        return result.ToArray();
    }
    public static BoilerSteamPartitionMetadata[] Read(int disk, Guid diskId, long capacity) {
        if (disk<0) throw new InvalidOperationException("Invalid disk number.");
        using (var handle=CreateFile(@"\\.\PhysicalDrive"+disk,0x80000000,3,IntPtr.Zero,3,0,IntPtr.Zero)) {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            var data=new byte[1024*1024]; uint returned;
            if (!DeviceIoControl(handle,0x70050,IntPtr.Zero,0,data,(uint)data.Length,out returned,IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
            return Parse(data,returned,diskId,capacity);
        }
    }
}
'@
}
function Get-SteamOsNativeMetadata($Disk, $Partitions) {
    Initialize-SteamPartitionReader
    $native = @([BoilerSteamPartitionReader]::Read([int]$Disk.Number, [guid]$Disk.Guid, [long]$Disk.Size))
    if ($native.Count -ne @($Partitions).Count) { throw 'Native and CIM partition counts differ.' }
    $result = @{}
    foreach ($part in $Partitions) {
        $same = @($native | Where-Object { $_.Id -eq [guid]$part.Guid })
        if ($same.Count -ne 1 -or $same[0].Number -ne $part.PartitionNumber -or $same[0].Type -ne [guid]$part.GptType -or $same[0].Offset -ne $part.Offset -or $same[0].Size -ne $part.Size) {
            throw 'Native and CIM GPT identities differ.'
        }
        $result[([guid]$part.Guid).ToString('D')] = $same[0]
    }
    return $result
}
# Pure snapshot recognizer, also used by the privileged batch-deletion preflight.
function Get-SteamOsPartitionStarts($Disk, [switch]$IncludeBootRemnants) {
    if ($Disk.style -cne 'GPT' -or $Disk.bus -notin @('NVMe','SATA','USB','SD','MMC') -or $Disk.sectorSize -ne 512 -or
        $Disk.isOffline -ne $false -or $Disk.isReadOnly -ne $false -or $Disk.partitionReadError) { return }
    $roles = @((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'steam-partition-roles.json') -Raw | ConvertFrom-Json))
    if ($roles.Count -ne 8) { throw 'Invalid SteamOS role policy.' }
    $parts = @($Disk.partitions | Sort-Object offset)
    $complete = @()
    for ($start = 0; $start -le $parts.Count - $roles.Count; $start++) {
        $matches = $true
        for ($i = 0; $i -lt $roles.Count; $i++) {
            $part = $parts[$start + $i]
            if ($part.nativeGptVerified -ne $true -or $part.gptName -cne $roles[$i].name -or [guid]$part.gptType -ne [guid]$roles[$i].gptType) { $matches = $false; break }
            if ($i -gt 0 -and [long]$part.offset -ne [long]$parts[$start + $i - 1].offset + [long]$parts[$start + $i - 1].size) { $matches = $false; break }
        }
        if ($matches) { for ($i = 0; $i -lt $roles.Count; $i++) { $complete += [long]$parts[$start + $i].offset } }
    }
    $complete
    if (-not $IncludeBootRemnants) { return }
    # Names are candidates, never deletion permission. Residual boot ownership
    # must also be established by the live, read-only boot-content inspection.
    $named = @($parts | Where-Object { $complete -notcontains [long]$_.offset -and $roles.name -ccontains $_.gptName })
    if ($named.Count -ne 3) { return }
    for ($i=0; $i -lt 3; $i++) {
        $part=$named[$i]
        if ($part.nativeGptVerified -ne $true -or $part.gptName -cne $roles[$i].name -or [guid]$part.gptType -ne [guid]$roles[$i].gptType -or
            [long]$roles[$i].bootSize -le 0 -or [long]$part.size -ne [long]$roles[$i].bootSize) { return }
        if ($i -gt 0 -and [long]$part.offset -ne [long]$named[$i-1].offset + [long]$named[$i-1].size) { return }
    }
    $named | ForEach-Object { [long]$_.offset }
}
function Get-SteamOsDeletionContext($Disk) {
    $complete = @(Get-SteamOsPartitionStarts $Disk)
    $recognized = @(Get-SteamOsPartitionStarts $Disk -IncludeBootRemnants)
    $context = @{}
    foreach ($part in $Disk.partitions) {
        if ($recognized -notcontains [long]$part.offset) { continue }
        $copy = $part | Select-Object *
        $copy | Add-Member NoteProperty completeSteamOsLayout ($complete -contains [long]$part.offset) -Force
        $key=([guid]$part.guid).ToString('D')
        if ([guid]$part.guid -eq [guid]::Empty -or $context.ContainsKey($key)) { throw 'Ambiguous SteamOS partition identity.' }
        $context[$key]=$copy
    }
    return $context
}
function Get-SteamOsDeletionMetadata($Disk, $Partition) {
    Initialize-SteamPartitionReader
    $native = @([BoilerSteamPartitionReader]::Read([int]$Disk.Number, [guid]$Disk.Guid, [long]$Disk.Size) |
        Where-Object { $_.Id -eq [guid]$Partition.Guid })
    if ($native.Count -ne 1) { throw 'SteamOS native partition identity is missing or ambiguous.' }
    return $native[0]
}
