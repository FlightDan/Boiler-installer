# Read-only inspection for a caller's already native-GPT-verified partition.
# Dot sourcing defines functions only: no compilation, enumeration or I/O.
function Initialize-SteamOsBootReader {
    if ('BoilerSteamBootNative' -as [type]) { return }
    # C# 5 / Windows PowerShell 5.1. Layout and bounds checks mirror
    # app/NativeVolumes.cs; no drive letter, mount point or write access is used.
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public sealed class BoilerSteamBootExtent {
    public int DiskNumber; public long Offset; public long Size;
}
public sealed class BoilerSteamBootVolume {
    public string Path; public BoilerSteamBootExtent[] Extents; public bool IsOptical;
}
public static class BoilerSteamBootNative {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool DeviceIoControl(SafeFileHandle handle, uint code, IntPtr input, uint inputSize, byte[] output, uint size, out uint returned, IntPtr overlap);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr FindFirstVolume(StringBuilder name, uint length);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool FindNextVolume(IntPtr search, StringBuilder name, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool FindVolumeClose(IntPtr search);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder name, uint length, uint flags);
    public static string CanonicalVolumePath(string path) {
        Guid id;
        if (path==null || path.Length!=49 || !path.StartsWith(@"\\?\Volume{",StringComparison.OrdinalIgnoreCase)
            || !path.EndsWith(@"}\",StringComparison.Ordinal) || !Guid.TryParseExact(path.Substring(11,36),"D",out id))
            throw new IOException("Expected a native volume GUID root.");
        return @"\\?\Volume{"+id.ToString("D")+@"}\";
    }
    public static string VolumeFromFinalPath(string path) {
        if (path==null || path.Length<49) throw new IOException("Invalid final handle path.");
        return CanonicalVolumePath(path.Substring(0,49));
    }
    public static BoilerSteamBootExtent[] ParseExtents(byte[] data, uint returned) {
        if (data==null || returned<8 || returned>data.Length) throw new IOException("Short or oversized native volume extents response.");
        uint count=BitConverter.ToUInt32(data,0);
        if (count==0 || count>(returned-8)/24) throw new IOException("Missing or invalid native volume extents.");
        var result=new BoilerSteamBootExtent[count];
        for (int i=0;i<count;i++) {
            int at=8+i*24; uint disk=BitConverter.ToUInt32(data,at);
            long offset=BitConverter.ToInt64(data,at+8), size=BitConverter.ToInt64(data,at+16);
            if (disk>int.MaxValue || offset<0 || size<=0 || offset>long.MaxValue-size)
                throw new IOException("Invalid native volume extent bounds.");
            result[i]=new BoilerSteamBootExtent { DiskNumber=(int)disk, Offset=offset, Size=size };
        }
        return result;
    }
    public static bool Matches(BoilerSteamBootExtent[] extents, int disk, long offset, long size) {
        if (disk<0 || offset<0 || size<=0 || offset>long.MaxValue-size) throw new IOException("Invalid target partition bounds.");
        if (extents==null || extents.Length!=1) throw new IOException("Missing or multiple native volume extents.");
        var e=extents[0];
        if (e==null || e.DiskNumber<0 || e.Offset<0 || e.Size<=0 || e.Offset>long.MaxValue-e.Size) throw new IOException("Invalid native volume extent bounds.");
        if (e.DiskNumber!=disk) return false;
        if (e.Offset==offset && e.Size==size) return true;
        if (e.Offset<offset+size && offset<e.Offset+e.Size) throw new IOException("Native volume only partially overlaps the reviewed partition.");
        return false;
    }
    static byte[] Query(SafeFileHandle handle, uint code, int length, int minimum) {
        var data=new byte[length]; uint returned;
        if (!DeviceIoControl(handle,code,IntPtr.Zero,0,data,(uint)length,out returned,IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(),"Read native volume metadata");
        if (returned<minimum || returned>data.Length) throw new IOException("Short or oversized native volume metadata response.");
        Array.Resize(ref data,(int)returned); return data;
    }
    public static BoilerSteamBootVolume ReadVolume(string path) {
        path=CanonicalVolumePath(path);
        using (var handle=CreateFile(path.Substring(0,path.Length-1),0,7,IntPtr.Zero,3,0,IntPtr.Zero)) {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(),"Open native volume metadata");
            uint deviceType=0;
            try { deviceType=BitConverter.ToUInt32(Query(handle,0x2d1080,12,12),0); }
            catch (Win32Exception) { } catch (IOException) { }
            // Only a successful explicit FILE_DEVICE_CD_ROM result can skip
            // extent parsing. All other failures proceed to the strict IOCTL.
            if (deviceType==2) return new BoilerSteamBootVolume { Path=path, IsOptical=true, Extents=new BoilerSteamBootExtent[0] };
            byte[] data=Query(handle,0x560000,65536,8);
            return new BoilerSteamBootVolume { Path=path, Extents=ParseExtents(data,(uint)data.Length) };
        }
    }
    public static string[] EnumerateVolumePaths() {
        var name=new StringBuilder(1024); IntPtr search=FindFirstVolume(name,1024);
        if (search==new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error(),"Begin native volume discovery");
        var result=new List<string>(); var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        try {
            while (true) {
                string path=CanonicalVolumePath(name.ToString());
                if (!seen.Add(path)) throw new IOException("Duplicate native volume enumeration.");
                result.Add(path);
                if (FindNextVolume(search,name,1024)) continue;
                int code=Marshal.GetLastWin32Error();
                if (code!=18) throw new Win32Exception(code,"Continue native volume discovery");
                break;
            }
        } finally { FindVolumeClose(search); }
        return result.ToArray();
    }
    public static string LocalHostPath(string path) {
        if (String.IsNullOrWhiteSpace(path) || path.IndexOf('\0')>=0) throw new IOException("Expected an absolute local host path.");
        if (path.StartsWith(@"\\?\",StringComparison.Ordinal)) {
            if (path.Length<49) throw new IOException("Expected a native volume host path.");
            CanonicalVolumePath(path.Substring(0,49));
        } else if (path.Length<3 || !Char.IsLetter(path[0]) || path[1]!=':' || path[2]!='\\') {
            throw new IOException("Expected an absolute local host path.");
        }
        return System.IO.Path.GetFullPath(path);
    }
    public static string MissingPathParent(string path) {
        // Called only for Win32 FILE_NOT_FOUND / PATH_NOT_FOUND, never for
        // ACCESS_DENIED, sharing errors or an unsupported final-path format.
        path=LocalHostPath(path); int root=path.StartsWith(@"\\?\",StringComparison.Ordinal) ? 49 : 3;
        string trimmed=path.TrimEnd('\\');
        if (trimmed.Length<root) throw new IOException("No existing host-path ancestor.");
        int at=trimmed.LastIndexOf('\\');
        if (at<root-1) throw new IOException("No existing host-path ancestor.");
        return at==root-1 ? path.Substring(0,root) : trimmed.Substring(0,at);
    }
    public static BoilerSteamBootVolume FromHostPath(string path) {
        path=LocalHostPath(path);
        // access=0 is metadata only; BACKUP_SEMANTICS supports directories.
        // Do not OPEN_REPARSE_POINT: bind the resolved file/directory, not
        // the drive letter or the link's containing filesystem.
        using (var handle=CreateFile(path,0,7,IntPtr.Zero,3,0x02000000,IntPtr.Zero)) {
            if (handle.IsInvalid) {
                int code=Marshal.GetLastWin32Error();
                // Null means ONLY CreateFile's FILE/PATH_NOT_FOUND. Keep this
                // distinct from access denial and final-path/extent failures.
                if (code==2 || code==3) return null;
                throw new Win32Exception(code,"Open host-path metadata");
            }
            var name=new StringBuilder(32768);
            uint length=GetFinalPathNameByHandle(handle,name,(uint)name.Capacity,1);
            if (length==0 || length>=name.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error(),"Resolve host handle volume");
            return ReadVolume(VolumeFromFinalPath(name.ToString()));
        }
    }
}
'@
}
# These three narrow environmental seams are replaced with throwing mocks by
# the synthetic test. Compilation / pure parsers never call them implicitly.
function Get-SteamOsBootNativeVolume([string]$Path) {
    Initialize-SteamOsBootReader
    [BoilerSteamBootNative]::ReadVolume($Path)
}
function Get-SteamOsBootNativeVolumePaths {
    Initialize-SteamOsBootReader
    [BoilerSteamBootNative]::EnumerateVolumePaths()
}
function Get-SteamOsBootNativeHostVolume([string]$Path) {
    Initialize-SteamOsBootReader
    [BoilerSteamBootNative]::FromHostPath($Path)
}
function Get-SteamOsBootTarget($Disk, $Partition) {
    $number=0; $offset=[long]0; $size=[long]0
    if ($null -eq $Disk -or $null -eq $Partition -or
        -not [int]::TryParse([string]$Disk.Number,[ref]$number) -or $number -lt 0 -or
        -not [long]::TryParse([string]$Partition.Offset,[ref]$offset) -or $offset -lt 0 -or
        -not [long]::TryParse([string]$Partition.Size,[ref]$size) -or $size -le 0 -or $offset -gt [long]::MaxValue-$size) {
        throw 'Invalid reviewed disk/partition bounds.'
    }
    if ($null -ne $Disk.PSObject.Properties['Size']) {
        $capacity=[long]0
        if (-not [long]::TryParse([string]$Disk.Size,[ref]$capacity) -or $capacity -le 0 -or $offset -gt $capacity-$size) { throw 'Reviewed partition is outside its disk.' }
    }
    [pscustomobject]@{Number=$number;Offset=$offset;Size=$size}
}
function Get-SteamOsBootRoot($Disk, $Partition, $Volume) {
    $target=Get-SteamOsBootTarget $Disk $Partition
    Initialize-SteamOsBootReader
    $preferred=''
    if ($null -ne $Volume -and $null -ne $Volume.PSObject.Properties['Path']) { $preferred=[string]$Volume.Path }
    if (-not [string]::IsNullOrWhiteSpace($preferred)) {
        $path=[BoilerSteamBootNative]::CanonicalVolumePath($preferred)
        $native=Get-SteamOsBootNativeVolume $path
        if ($null -eq $native -or $native.IsOptical -or
            [BoilerSteamBootNative]::CanonicalVolumePath($native.Path) -cne $path -or
            -not [BoilerSteamBootNative]::Matches($native.Extents,$target.Number,$target.Offset,$target.Size)) { throw 'Supplied volume is not uniquely bound to the reviewed partition.' }
        return $path
    }
    $found=@(); $seen=@{}
    foreach ($candidate in @(Get-SteamOsBootNativeVolumePaths)) {
        $path=[BoilerSteamBootNative]::CanonicalVolumePath($candidate)
        if ($seen.ContainsKey($path)) { throw 'Duplicate native volume enumeration.' }; $seen[$path]=$true
        $native=Get-SteamOsBootNativeVolume $path
        if ($null -eq $native -or [BoilerSteamBootNative]::CanonicalVolumePath($native.Path) -cne $path) { throw 'Native volume identity changed.' }
        if ($native.IsOptical) { continue }
        if ([BoilerSteamBootNative]::Matches($native.Extents,$target.Number,$target.Offset,$target.Size)) { $found+=@($path) }
    }
    if ($found.Count -ne 1) { throw 'No unique native volume for the reviewed partition.' }
    return $found[0]
}
function Test-SteamOsBootHostPath($Disk, $Partition, [string]$Path) {
    $target=Get-SteamOsBootTarget $Disk $Partition
    Initialize-SteamOsBootReader
    $pathToOpen=[BoilerSteamBootNative]::LocalHostPath($Path)
    while ($true) {
        $native=Get-SteamOsBootNativeHostVolume $pathToOpen
        if ($null -ne $native) { break }
        $pathToOpen=[BoilerSteamBootNative]::MissingPathParent($pathToOpen)
    }
    if ($native.IsOptical) { throw 'Host-path volume binding is unavailable.' }
    $null=[BoilerSteamBootNative]::CanonicalVolumePath($native.Path)
    return [BoilerSteamBootNative]::Matches($native.Extents,$target.Number,$target.Offset,$target.Size)
}
function Get-SteamOsBootRefindFiles {
    # Exact deployed ownership paths from components/refind/manage.py FILES.
    # This is a deletion-ownership list, not a second deployment implementation.
    $own='efi/oxp3-refind/'
    $files=@($own+'steamcl-original.efi';$own+'refind_x64.efi';$own+'manage.py')
    foreach ($name in @('os_linux.png','os_win.png','func_shutdown.png','func_about.png','func_reset.png','func_firmware.png','vol_internal.png','vol_external.png','vol_optical.png','vol_net.png','vol_efi.png')) { $files+=@($own+'icons/'+$name) }
    foreach ($name in @('background.png','selection-big.png','selection-small.png','font.png')) { $files+=@($own+'theme/'+$name) }
    $files+=@($own+'drivers_x64/usbxbox360dxe.efi';$own+'gamepad.ini';'efi/xbox360/config.ini';$own+'refind.conf';'efi/boot/refind.conf';'efi/boot/bootx64.efi')
    return $files
}
function Get-SteamOsBootContentBlockReason([string]$Root, [string]$Role, [bool]$RequireOwnership=$true) {
    if ($Role -cnotin @('esp','efi-A','efi-B')) { throw 'Unsupported SteamOS boot partition role.' }
    if ([string]::IsNullOrWhiteSpace($Root)) { throw 'Missing boot inspection root.' }
    # Positive SteamOS ownership evidence is no longer required: recognition is
    # name + count + contiguity. What remains is the destructive-action guard —
    # another operating system's boot content on this partition means deleting it
    # can break that system, and the erase is not reversible.
    # $RequireOwnership is retained for call compatibility and has no effect.
    $rootItem=Get-Item -LiteralPath $Root -Force -ErrorAction Stop
    if (-not $rootItem.PSIsContainer) { throw 'Boot inspection root is not a directory.' }
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return 'Boot root is a reparse point.' }
    $queue=New-Object 'System.Collections.Generic.Queue[object]'
    $queue.Enqueue([pscustomobject]@{FullName=$rootItem.FullName;Relative=''})
    $fallback=$null; $loaders=@()
    $refindFiles=@(Get-SteamOsBootRefindFiles)+@('efi/oxp3-refind/state.json'); $refindDirectories=@{}
    foreach ($file in $refindFiles) {
        $parent=$file
        while ($parent.Contains('/')) { $parent=$parent.Substring(0,$parent.LastIndexOf('/')); $refindDirectories[$parent]=$true }
    }
    while ($queue.Count -gt 0) {
        $directory=$queue.Dequeue()
        foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force -ErrorAction Stop)) {
            $relative=([string]$directory.Relative + [string]$item.Name).ToLowerInvariant()
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return ('Boot content contains a reparse point: '+$relative+'.') }
            if ($relative -match '(^|/)(microsoft|windows|ubuntu|debian|fedora|opensuse|systemd)(/|$)' -or
                $item.Name -match '^(bootmgfw|bootmgr|memtest)(\.(efi|exe))?$' -or $item.Name -ieq 'BCD') { return ('Other operating-system boot content: '+$relative+'.') }
            # Any EFI vendor directory other than the ones this project owns is
            # another system's boot namespace. Erasing it breaks that system.
            # A stray file directly under EFI/ is not a vendor namespace.
            if ($item.PSIsContainer -and $relative -match '^efi/([^/]+)$') {
                $vendor=$Matches[1]
                if (@('steamos','boot','oxp3-refind','xbox360') -notcontains $vendor -and
                    -not ($Role -ceq 'esp' -and $refindDirectories.ContainsKey('efi/'+$vendor))) {
                    return ('Other operating-system boot content: '+$relative+'.')
                }
            }
            $managedRefind=$Role -ceq 'esp' -and ($relative -cin $refindFiles -or $refindDirectories.ContainsKey($relative))
            if ($item.PSIsContainer) {
                if ($relative -in @('efi/boot/bootx64.efi','efi/steamos/steamcl.efi','efi/steamos/grub.cfg') -or $relative -cin $refindFiles) { return ('Expected a boot file: '+$relative+'.') }
                $queue.Enqueue([pscustomobject]@{FullName=$item.FullName;Relative=$relative+'/'})
            } elseif ($relative -eq 'efi/boot/bootx64.efi') {
                $fallback=$item
            } elseif ($relative -in @('efi/steamos/steamcl.efi','efi/steamos/grubx64.efi','efi/oxp3-refind/refind_x64.efi')) {
                $loaders+=@($item)
            } elseif ($item.Extension -ieq '.efi' -and -not $relative.StartsWith('efi/steamos/') -and $relative -cnotin $refindFiles) {
                return ('Shared or unrecognized boot content: '+$relative+'.')
            } elseif ($relative -in @('efi/oxp3-refind/refind.conf','efi/boot/refind.conf')) {
                # The project menu may refer to Windows on another ESP. Any
                # local Windows loader would already block by path above.
                $config=Get-Content -LiteralPath $item.FullName -Raw -ErrorAction Stop
                if ($config -match '(?im)^\s*(chainloader|include)\s+' -or
                    $config -match '(?im)^\s*loader\s+(?!/EFI/(?:steamos/steamcl\.efi|Microsoft/Boot/bootmgfw\.efi)\s*$)\S') {
                    return ('Shared operating-system boot configuration: '+$relative+'.')
                }
            } elseif (-not $managedRefind -and ($item.Extension -in @('.cfg','.conf') -or $relative.StartsWith('steamos/conf/'))) {
                # Text boot settings can explicitly share another OS. No command or
                # file found here is run.
                $text=Get-Content -LiteralPath $item.FullName -Raw -ErrorAction Stop
                if ($text -match '(?i)\b(chainloader|microsoft|windows|ubuntu|debian|fedora|refind|systemd-boot)\b') { return ('Shared operating-system boot configuration: '+$relative+'.') }
            }
        }
    }
    if ($null -ne $fallback) {
        # A standalone fallback may be another OS's only loader. Compare it
        # with a loader already present in a SteamOS or project namespace.
        if ($fallback.Length -le 0 -or $loaders.Count -eq 0) { return 'SteamOS fallback ownership evidence is missing.' }
        $digest=(Get-FileHash -LiteralPath $fallback.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
        foreach ($loader in $loaders) {
            if ($loader.Length -eq $fallback.Length -and (Get-FileHash -LiteralPath $loader.FullName -Algorithm SHA256 -ErrorAction Stop).Hash -eq $digest) { return '' }
        }
        return 'Fallback loader is not bound to this volume SteamOS deployment.'
    }
    return ''
}
