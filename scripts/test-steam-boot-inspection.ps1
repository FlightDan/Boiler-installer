param([Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2.0
$checks=New-Object 'System.Collections.Generic.List[string]'
function Assert-Check([bool]$Ok,[string]$Name) {
    if (-not $Ok) { throw ('Failed: '+$Name) }
    $checks.Add($Name)
}
function Assert-Refused([scriptblock]$Action,[string]$Name,[string]$Message='') {
    $refused=$false
    try { & $Action | Out-Null } catch {
        if ($Message -and $_.Exception.ToString() -notmatch $Message) { throw }
        $refused=$true
    }
    Assert-Check $refused $Name
}
# No device/environment entry point is available, even accidentally. The only
# native code executed below is compilation plus pure path/byte/bounds parsers.
function Get-Disk { throw 'Real disk access forbidden in synthetic checks.' }
function Get-Partition { throw 'Real partition access forbidden in synthetic checks.' }
function Get-Volume { throw 'Real volume access forbidden in synthetic checks.' }
function Mount-DiskImage { throw 'Mount forbidden in synthetic checks.' }
function Remove-Partition { throw 'Deletion forbidden in synthetic checks.' }
function Add-PartitionAccessPath { throw 'Mount point assignment forbidden in synthetic checks.' }
Assert-Check (-not ('BoilerSteamBootNative' -as [type])) 'fresh-process-has-no-native-reader'
. (Join-Path $PSScriptRoot 'steam-boot-inspection.ps1')
Assert-Check (-not ('BoilerSteamBootNative' -as [type])) 'dot-source-does-not-compile-or-open-devices'
$script:volumeMode='forbidden'; $script:hostMode='forbidden'; $script:pathsMode='forbidden'
$script:volumes=@{}; $script:paths=@(); $script:hostResult=$null; $script:opened=@(); $script:volumeReads=@()
function Get-SteamOsBootNativeVolume([string]$Path) {
    if ($script:volumeMode -ne 'mock') { throw 'Real native volume read forbidden in synthetic checks.' }
    $script:volumeReads+=@($Path)
    if (-not $script:volumes.ContainsKey($Path)) { throw 'Synthetic volume metadata unavailable.' }
    $value=$script:volumes[$Path]
    if ($value -is [Exception]) { throw $value }
    return $value
}
function Get-SteamOsBootNativeVolumePaths {
    if ($script:pathsMode -ne 'mock') { throw 'Real native volume enumeration forbidden in synthetic checks.' }
    return $script:paths
}
function Get-SteamOsBootNativeHostVolume([string]$Path) {
    if ($script:hostMode -eq 'forbidden') { throw 'Real native host-path read forbidden in synthetic checks.' }
    $script:opened+=@($Path)
    if ($script:hostMode -eq 'denied') { throw (New-Object ComponentModel.Win32Exception 5) }
    if ($script:hostMode -eq 'sharing') { throw (New-Object ComponentModel.Win32Exception 32) }
    if ($script:hostMode -eq 'missing') { return $null }
    if ($script:hostMode -eq 'ancestor' -and $Path -ne 'Q:\host') { return $null }
    return $script:hostResult
}
Assert-Refused { Get-SteamOsBootNativeVolume 'unused' } 'native-volume-seam-forbidden-by-default' 'forbidden'
Assert-Refused { Get-SteamOsBootNativeVolumePaths } 'native-enumeration-seam-forbidden-by-default' 'forbidden'
Assert-Refused { Get-SteamOsBootNativeHostVolume 'unused' } 'native-host-seam-forbidden-by-default' 'forbidden'
Initialize-SteamOsBootReader
$guid='4e415449-5645-4750-5421-53594e544845'
$root='\\?\Volume{'+$guid+'}\'
$other='\\?\Volume{8b254965-60d8-4fb7-9309-851934ca198d}\'
$third='\\?\Volume{f58c25c4-4540-4494-b30f-dc28e0bd53d1}\'
Assert-Check ([BoilerSteamBootNative]::CanonicalVolumePath($root.ToUpperInvariant()) -ceq $root) 'canonical-volume-guid-case'
Assert-Check ([BoilerSteamBootNative]::VolumeFromFinalPath($root+'host\file.dmp') -ceq $root) 'final-handle-path-extracts-volume-guid'
foreach ($invalid in @($null,'','Q:\',('\\.\Volume{'+$guid+'}\'),($root.TrimEnd('\')),($root+'x'),($root.Replace($guid,'not-a-guid')),('\\server\share\'))) {
    Assert-Refused { [BoilerSteamBootNative]::CanonicalVolumePath($invalid) } ('reject-volume-root:'+ [string]$invalid)
}
Assert-Refused { [BoilerSteamBootNative]::VolumeFromFinalPath('\\?\UNC\server\share\host\file.dmp') } 'reject-final-unc-path'
foreach ($invalid in @('relative.dmp','Q:relative.dmp','\Windows\file','\\server\share\file','\\.\PhysicalDrive0','\\?\GLOBALROOT\Device\HarddiskVolume1\file')) {
    Assert-Refused { [BoilerSteamBootNative]::LocalHostPath($invalid) } ('reject-nonlocal-or-relative-host-path:'+ $invalid)
}
Assert-Check ([BoilerSteamBootNative]::MissingPathParent('Q:\host\future\dump.dmp') -eq 'Q:\host\future') 'missing-file-parent'
Assert-Check ([BoilerSteamBootNative]::MissingPathParent('Q:\host') -eq 'Q:\') 'missing-child-reaches-drive-root'
Assert-Check ([BoilerSteamBootNative]::MissingPathParent($root+'host') -ceq $root) 'missing-child-reaches-native-root'
Assert-Refused { [BoilerSteamBootNative]::MissingPathParent('Q:\') } 'missing-drive-root-is-not-safe'
Assert-Refused { [BoilerSteamBootNative]::MissingPathParent($root) } 'missing-volume-root-is-not-safe'
# Synthetic VOLUME_DISK_EXTENTS buffers only; no FindForDisk/device call exists.
function New-ExtentBytes([uint32]$Count=1) {
    $bytes=New-Object byte[] (8+24*$Count)
    [Array]::Copy([BitConverter]::GetBytes($Count),0,$bytes,0,4)
    for ($i=0;$i -lt $Count;$i++) {
        $at=8+24*$i
        [Array]::Copy([BitConverter]::GetBytes([uint32]7),0,$bytes,$at,4)
        [Array]::Copy([BitConverter]::GetBytes([long](1048576+$i*67108864)),0,$bytes,($at+8),8)
        [Array]::Copy([BitConverter]::GetBytes([long]67108864),0,$bytes,($at+16),8)
    }
    return ,$bytes
}
$bytes=New-ExtentBytes
$extents=[BoilerSteamBootNative]::ParseExtents($bytes,$bytes.Length)
Assert-Check ($extents.Count -eq 1 -and $extents[0].DiskNumber -eq 7 -and $extents[0].Offset -eq 1048576 -and $extents[0].Size -eq 67108864) 'pure-native-parser-exact-extent'
foreach ($returned in @(0,7,31,33)) { Assert-Refused { [BoilerSteamBootNative]::ParseExtents($bytes,$returned) } ('reject-extent-returned-length:'+ $returned) }
foreach ($change in @('zero-count','large-count','large-disk','negative-offset','zero-size','negative-size','overflow')) {
    $bad=New-ExtentBytes
    switch ($change) {
        'zero-count' { [Array]::Copy([BitConverter]::GetBytes([uint32]0),0,$bad,0,4) }
        'large-count' { [Array]::Copy([BitConverter]::GetBytes([uint32]2),0,$bad,0,4) }
        'large-disk' { [Array]::Copy([BitConverter]::GetBytes([uint32]::MaxValue),0,$bad,8,4) }
        'negative-offset' { [Array]::Copy([BitConverter]::GetBytes([long]-1),0,$bad,16,8) }
        'zero-size' { [Array]::Copy([BitConverter]::GetBytes([long]0),0,$bad,24,8) }
        'negative-size' { [Array]::Copy([BitConverter]::GetBytes([long]-1),0,$bad,24,8) }
        'overflow' { [Array]::Copy([BitConverter]::GetBytes([long]::MaxValue),0,$bad,16,8) }
    }
    Assert-Refused { [BoilerSteamBootNative]::ParseExtents($bad,$bad.Length) } ('reject-native-extent:'+ $change)
}
$multiBytes=New-ExtentBytes 2
$multi=[BoilerSteamBootNative]::ParseExtents($multiBytes,$multiBytes.Length)
Assert-Check ($multi.Count -eq 2) 'parser-retains-multiple-extents-for-explicit-refusal'
Assert-Refused { [BoilerSteamBootNative]::Matches($multi,7,1048576,67108864) } 'binding-refuses-multiple-extents'
Assert-Refused { [BoilerSteamBootNative]::Matches($extents,7,1048576,33554432) } 'binding-refuses-overlapping-different-size'
Assert-Check (-not [BoilerSteamBootNative]::Matches($extents,8,1048576,67108864)) 'binding-distinguishes-disk-number'
Assert-Check (-not [BoilerSteamBootNative]::Matches($extents,7,134217728,67108864)) 'binding-distinguishes-disjoint-partition'
function New-NativeVolume([string]$Path,$Extents,[bool]$Optical=$false) {
    $value=New-Object BoilerSteamBootVolume
    $value.Path=$Path; $value.Extents=$Extents; $value.IsOptical=$Optical
    return $value
}
$disk=[pscustomobject]@{Number=7;Size=[long]1073741824}
$part=[pscustomobject]@{Offset=[long]1048576;Size=[long]67108864}
$volume=New-NativeVolume $root $extents
$script:volumeMode='mock'; $script:pathsMode='mock'
$script:volumes=@{}; $script:volumes[$root]=$volume
$script:paths=@($root)
Assert-Check ((Get-SteamOsBootRoot $disk $part ([pscustomobject]@{Path=$root})) -ceq $root) 'supplied-guid-root-is-natively-rebound'
Assert-Check ($script:volumeReads.Count -eq 1) 'supplied-path-was-not-trusted-without-native-read'
$script:pathsMode='forbidden'
Assert-Check ((Get-SteamOsBootRoot $disk $part ([pscustomobject]@{Path=$root})) -ceq $root) 'preferred-volume-does-not-enumerate'
$script:pathsMode='mock'
Assert-Check ((Get-SteamOsBootRoot $disk $part $null) -ceq $root) 'no-volume-path-discovers-native-guid-root'
Assert-Check ((Get-SteamOsBootRoot $disk $part ([pscustomobject]@{Path=''})) -ceq $root) 'empty-volume-path-is-not-a-safety-verdict'
$script:volumes[$other]=New-NativeVolume $other $extents
$script:paths=@($root,$other)
Assert-Refused { Get-SteamOsBootRoot $disk $part $null } 'ambiguous-matching-volume-roots-refused'
$script:paths=@($root,$root)
Assert-Refused { Get-SteamOsBootRoot $disk $part $null } 'duplicate-volume-enumeration-refused'
$script:paths=@()
Assert-Refused { Get-SteamOsBootRoot $disk $part $null } 'no-native-volume-is-not-safe'
$script:volumes[$other]=New-NativeVolume $other $multi
$script:paths=@($root,$other)
Assert-Refused { Get-SteamOsBootRoot $disk $part $null } 'enumeration-does-not-drop-multiple-extents'
$script:volumes[$other]=New-Object IO.IOException 'Synthetic corrupt extent response'
Assert-Refused { Get-SteamOsBootRoot $disk $part $null } 'enumeration-does-not-swallow-corrupt-extent' 'corrupt extent'
$script:volumes[$other]=New-NativeVolume $other @() $true
Assert-Check ((Get-SteamOsBootRoot $disk $part $null) -ceq $root) 'explicit-optical-volume-may-be-skipped'
Assert-Refused { Get-SteamOsBootRoot $disk $part ([pscustomobject]@{Path=$other}) } 'preferred-optical-volume-is-not-a-partition'
Assert-Refused { Get-SteamOsBootRoot $disk $part ([pscustomobject]@{Path='Q:\'}) } 'preferred-drive-letter-is-not-a-native-root'
$script:volumes[$third]=New-NativeVolume $root $extents
Assert-Refused { Get-SteamOsBootRoot $disk $part ([pscustomobject]@{Path=$third}) } 'native-path-substitution-refused'
foreach ($badPart in @([pscustomobject]@{Offset=-1;Size=1},[pscustomobject]@{Offset=1;Size=0},[pscustomobject]@{Offset='1.5';Size=1},[pscustomobject]@{Offset=[long]::MaxValue;Size=1},[pscustomobject]@{Offset=1073741824;Size=512})) {
    Assert-Refused { Get-SteamOsBootRoot $disk $badPart $null } 'invalid-target-bounds-refused'
}
# The fake Q: names below are passed ONLY to pure path manipulation and the
# mocked native seam. They are never opened, probed with Test-Path or mounted.
$script:hostMode='mock'; $script:hostResult=$volume
Assert-Check (Test-SteamOsBootHostPath $disk $part 'Q:\host\pagefile.sys') 'handle-bound-host-volume-protects-partition'
$script:hostResult=New-NativeVolume $other ([BoilerSteamBootNative]::ParseExtents((New-ExtentBytes),32))
$script:hostResult.Extents[0].DiskNumber=8
Assert-Check (-not (Test-SteamOsBootHostPath $disk $part 'Q:\host\file')) 'same-string-drive-can-resolve-to-different-native-disk'
$script:hostResult=$volume; $script:hostMode='ancestor'; $script:opened=@()
Assert-Check (Test-SteamOsBootHostPath $disk $part 'Q:\host\future\dump.dmp') 'nonexistent-dump-binds-nearest-existing-parent'
Assert-Check (($script:opened -join '|') -eq 'Q:\host\future\dump.dmp|Q:\host\future|Q:\host') 'missing-tail-walk-is-exact'
foreach ($mode in @('denied','sharing')) {
    $script:hostMode=$mode; $script:opened=@()
    Assert-Refused { Test-SteamOsBootHostPath $disk $part 'Q:\host\dump.dmp' } ('host-error-is-not-nonexistence:'+ $mode)
    Assert-Check ($script:opened.Count -eq 1) ('host-error-does-not-walk-to-parent:'+ $mode)
}
$script:hostMode='missing'
Assert-Refused { Test-SteamOsBootHostPath $disk $part 'Q:\host\dump.dmp' } 'missing-all-host-ancestors-refused'
$script:hostMode='mock'; $script:hostResult=New-NativeVolume $root $multi
Assert-Refused { Test-SteamOsBootHostPath $disk $part 'Q:\host' } 'spanned-host-volume-refused'
$script:hostResult=New-NativeVolume 'Q:\' $extents
Assert-Refused { Test-SteamOsBootHostPath $disk $part 'Q:\host' } 'host-final-volume-format-refused'
$script:hostMode='forbidden'; $script:volumeMode='forbidden'; $script:pathsMode='forbidden'
# Only tiny ordinary files owned by this test are created; all are preserved,
# and there is deliberately no deletion/cleanup or device-volume operation.
if (-not [IO.Path]::IsPathRooted($ReportDirectory)) { throw 'ReportDirectory must be absolute.' }
$run=Join-Path $ReportDirectory ('synthetic-'+[guid]::NewGuid().ToString('N'))
$null=[IO.Directory]::CreateDirectory($run)
function New-BootFixture([string]$Name,[hashtable]$Files) {
    $directory=Join-Path $run ($Name+'-'+[guid]::NewGuid().ToString('N'))
    $null=[IO.Directory]::CreateDirectory($directory)
    foreach ($relative in $Files.Keys) {
        $path=Join-Path $directory $relative
        $null=[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path))
        [IO.File]::WriteAllText($path,[string]$Files[$relative])
    }
    return $directory
}
$grub="menuentry 'SteamOS' {`n steamenv_boot /boot/vmlinuz-linux`n}`n"
foreach ($role in @('efi-A','efi-B')) {
    $fixture=New-BootFixture $role @{'EFI/steamos/grub.cfg'=$grub;'EFI/steamos/grubx64.efi'='synthetic owned namespace, NOT an executable';'SteamOS/partsets/fixture'='rootfs-A 05662c67-b478-4f77-a9bb-45f7026583e3'}
    Assert-Check ((Get-SteamOsBootContentBlockReason $fixture $role) -ceq '') ('positive-guest-slot-path-evidence:'+ $role)
}
$esp=New-BootFixture 'esp' @{'SteamOS/conf/fixture.conf'='title SteamOS'}
Assert-Check ((Get-SteamOsBootContentBlockReason $esp 'esp') -ceq '') 'positive-guest-esp-conf-namespace-evidence'
# An empty partition is no longer blocked: recognition is name + count +
# contiguity, and only another system's boot content is a deletion barrier.
$empty=New-BootFixture 'empty' @{}
foreach ($role in @('esp','efi-A','efi-B')) { Assert-Check ((Get-SteamOsBootContentBlockReason $empty $role) -ceq '') ('empty-root-is-not-an-ownership-gate:'+ $role) }
$efiOnly=New-BootFixture 'efi-only' @{'EFI/empty-note'='fixture'}
Assert-Check ((Get-SteamOsBootContentBlockReason $efiOnly 'efi-A') -ceq '') 'efi-directory-alone-is-not-a-deletion-barrier'
$noMarkers=New-BootFixture 'grub-no-steamos-entry' @{'EFI/steamos/grub.cfg'="menuentry 'generic' {}"}
Assert-Check ((Get-SteamOsBootContentBlockReason $noMarkers 'efi-A') -ceq '') 'named-grub-file-without-generated-entry-is-not-a-barrier'
foreach ($entry in @('EFI/Microsoft/Boot/bootmgfw.efi','EFI/ubuntu/grubx64.efi','EFI/unknown/other.efi','EFI/steamos/bootmgfw.efi','EFI/steamos/BCD','bootmgr')) {
    $files=@{'EFI/steamos/grub.cfg'=$grub}; $files[$entry]='synthetic unrelated loader'
    $fixture=New-BootFixture 'shared-slot' $files
    Assert-Check ([bool](Get-SteamOsBootContentBlockReason $fixture 'efi-A')) ('shared-or-unknown-boot-content-protected:'+ $entry)
}
# Project files can be recognized without a current installation journal.
$ownRefind=New-BootFixture 'own-refind' @{'EFI/steamos/grub.cfg'=$grub;'EFI/OXP3-rEFInd/refind_x64.efi'='synthetic managed loader'}
Assert-Check ((Get-SteamOsBootContentBlockReason $ownRefind 'efi-A') -ceq '') 'own-refind-directory-is-not-a-deletion-barrier'
# Both boot slots can use a fallback that matches their SteamOS loader.
$genericFallback=New-BootFixture 'slot-fallback' @{'EFI/steamos/grubx64.efi'='synthetic loader';'EFI/BOOT/BOOTX64.EFI'='synthetic loader'}
foreach ($role in @('efi-A','efi-B')) { Assert-Check ((Get-SteamOsBootContentBlockReason $genericFallback $role) -ceq '') ('same-volume-slot-fallback-allowed:'+ $role) }
$espFallback=New-BootFixture 'esp-shared-fallback' @{'SteamOS/conf/fixture.conf'='title SteamOS';'EFI/BOOT/BOOTX64.EFI'='not-proof-of-SteamOS'}
Assert-Check ((Get-SteamOsBootContentBlockReason $espFallback 'esp') -match 'fallback ownership evidence') 'standalone-fallback-stays-protected'
$officialFiles=@{'SteamOS/conf/fixture.conf'='title SteamOS';'EFI/steamos/steamcl.efi'='synthetic-official-loader';'EFI/BOOT/BOOTX64.EFI'='synthetic-official-loader'}
$officialEsp=New-BootFixture 'steamcl-byte-bound-fallback' $officialFiles
Assert-Check ((Get-SteamOsBootContentBlockReason $officialEsp 'esp') -ceq '') 'steamcl-fallback-still-allowed'
$officialFiles['EFI/BOOT/BOOTX64.EFI']='synthetic-different-loader'
$fixture=New-BootFixture 'unbound-fallback' $officialFiles
Assert-Check ((Get-SteamOsBootContentBlockReason $fixture 'esp') -match 'Fallback loader is not bound') 'different-fallback-stays-protected'
$officialFiles['EFI/BOOT/BOOTX64.EFI']='synthetic-official-loader'; $officialFiles['EFI/BOOT/other.efi']='unknown-extra'
$fixture=New-BootFixture 'extra-fallback-content' $officialFiles
Assert-Check ((Get-SteamOsBootContentBlockReason $fixture 'esp') -match 'Shared or unrecognized') 'unknown-efi-fallback-sibling-stays-protected'
foreach ($role in @('esp','efi-A','efi-B')) { Assert-Check ((Get-SteamOsBootContentBlockReason $empty $role $false) -ceq '') ('relaxed-caller-flag-is-compatible:'+ $role) }
Assert-Check ((Get-SteamOsBootContentBlockReason $espFallback 'esp' $false) -match 'fallback ownership evidence') 'relaxed-caller-flag-cannot-bypass-fallback-check'
# A vendor directory this project does not own is another system's namespace.
$foreignVendor=New-BootFixture 'foreign-vendor' @{'EFI/Fedora/shimx64.efi'='other OS'}
Assert-Check ((Get-SteamOsBootContentBlockReason $foreignVendor 'esp') -match 'Other operating-system') 'unknown-efi-vendor-directory-protected'
$notBcd=New-BootFixture 'not-bcd' @{'EFI/not-bcd-file'='plain file'}
Assert-Check ((Get-SteamOsBootContentBlockReason $notBcd 'esp') -ceq '') 'near-miss-name-is-not-a-barrier'
$unknownEfi=New-BootFixture 'unknown-efi' @{'EFI/unrecognized.efi'='unknown loader'}
Assert-Check ((Get-SteamOsBootContentBlockReason $unknownEfi 'esp') -match 'Shared or unrecognized') 'efi-root-loader-protected'
# Match the actual current manage.py FILES layout and byte-journal contract.
# These are ordinary text stand-ins for binaries, not executable validation.
$managedFiles=@(Get-SteamOsBootRefindFiles)
Assert-Check ($managedFiles.Count -eq 24) 'current-refind-managed-inventory-has-24-files'
$refindConfig="scanfor manual`nmenuentry `"SteamOS`" {`n volume 11223344-5566-7788-99aa-bbccddeeff00`n loader /EFI/steamos/steamcl.efi`n}`nmenuentry `"Windows`" {`n volume 22334455-6677-8899-aabb-ccddeeff0011`n loader /EFI/Microsoft/Boot/bootmgfw.efi`n}`n"
function New-RefindFixture([string]$Variant) {
    $files=@{'SteamOS/conf/fixture.conf'='title SteamOS';'EFI/steamos/steamcl.efi'='synthetic-official-loader'}
    foreach ($relative in $managedFiles) { $files[$relative]='synthetic-managed:'+ $relative }
    $files['efi/oxp3-refind/steamcl-original.efi']='synthetic-official-loader'
    $files['efi/boot/bootx64.efi']=$files['efi/oxp3-refind/refind_x64.efi']
    $files['efi/oxp3-refind/refind.conf']=$refindConfig
    $files['efi/boot/refind.conf']=$refindConfig
    $desired=[ordered]@{}
    foreach ($relative in $managedFiles) { $desired[$relative]=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$files[$relative])) }
    $state=[ordered]@{schema=1;phase='installed';esp='11223344-5566-7788-99aa-bbccddeeff00';windows='22334455-6677-8899-aabb-ccddeeff0011';desired=$desired}
    switch ($Variant) {
        'changed' { $files['efi/boot/bootx64.efi']='edited-loader' }
        'interrupted' { $state.phase='installing' }
        'same-esp-windows' { $state.windows=$state.esp }
        'same-volume-microsoft' { $files['EFI/Microsoft/Boot/bootmgfw.efi']='other OS on same ESP' }
        'unknown-extra' { $files['EFI/OXP3-rEFInd/extra.efi']='unknown' }
        'unknown-menu' { $files['EFI/OXP3-rEFInd/refind.conf']="scanfor manual`nloader /other.efi`n" }
        'bad-journal' { $desired['efi/boot/bootx64.efi']='not-base64!' }
    }
    $files['efi/oxp3-refind/state.json']=$state | ConvertTo-Json -Depth 6
    New-BootFixture ('managed-refind-'+$Variant) $files
}
$managedEsp=New-RefindFixture 'installed'
Assert-Check ((Get-SteamOsBootContentBlockReason $managedEsp 'esp') -ceq '') 'managed-refind-fallback-with-cross-volume-windows-menu-positive'
# The project rEFInd journal is no longer an ownership gate. Variants are now
# judged only by whether they carry another system's boot content.
foreach ($variant in @('interrupted','same-esp-windows')) {
    $fixture=New-RefindFixture $variant
    Assert-Check ((Get-SteamOsBootContentBlockReason $fixture 'esp') -ceq '') ('managed-refind-variant-no-longer-blocked:'+ $variant)
}
foreach ($variant in @('changed','unknown-extra','unknown-menu')) {
    Assert-Check ([bool](Get-SteamOsBootContentBlockReason (New-RefindFixture $variant) 'esp')) ('unrecognized-refind-loader-or-menu-protected:'+ $variant)
}
$fixture=New-RefindFixture 'same-volume-microsoft'
Assert-Check ((Get-SteamOsBootContentBlockReason $fixture 'esp') -match 'Other operating-system') 'managed-refind-same-volume-microsoft-protected'
$fixture=New-RefindFixture 'bad-journal'
Assert-Check ((Get-SteamOsBootContentBlockReason $fixture 'esp') -ceq '') 'malformed-refind-journal-is-not-an-ownership-gate'
$sharedConfig=New-BootFixture 'shared-grub' @{'EFI/steamos/grub.cfg'=($grub+"`nchainloader /EFI/Microsoft/Boot/bootmgfw.efi")}
Assert-Check ((Get-SteamOsBootContentBlockReason $sharedConfig 'efi-A') -match 'Shared operating-system') 'shared-grub-chainloader-protected'
$sharedConf=New-BootFixture 'shared-esp-conf' @{'SteamOS/conf/fixture.conf'='title Windows'}
Assert-Check ((Get-SteamOsBootContentBlockReason $sharedConf 'esp') -match 'Shared operating-system') 'shared-esp-conf-protected'
Assert-Refused { Get-SteamOsBootContentBlockReason $esp 'rootfs-A' } 'unsupported-role-refused'
Assert-Refused { Get-SteamOsBootContentBlockReason $esp 'EFI-A' } 'role-spelling-is-exact'
Assert-Refused { Get-SteamOsBootContentBlockReason (Join-Path $run 'not-created') 'esp' } 'unreadable-or-missing-root-throws'
# Attribute and permission failures are mocked: no ACL or reparse point is
# changed on this machine. The module-qualified cmdlets only see our fixtures.
$script:fileMode='normal'
function Get-Item {
    [CmdletBinding()]param([string]$LiteralPath,[switch]$Force)
    if ($script:fileMode -eq 'root-denied') { throw (New-Object UnauthorizedAccessException 'Synthetic root denied') }
    if ($script:fileMode -eq 'root-reparse') { return [pscustomobject]@{PSIsContainer=$true;FullName=$LiteralPath;Attributes=[IO.FileAttributes]::ReparsePoint} }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
}
function Get-ChildItem {
    [CmdletBinding()]param([string]$LiteralPath,[switch]$Force)
    if ($script:fileMode -eq 'listing-denied') { throw (New-Object UnauthorizedAccessException 'Synthetic listing denied') }
    if ($script:fileMode -eq 'child-reparse') { return [pscustomobject]@{Name='EFI';PSIsContainer=$true;FullName=(Join-Path $LiteralPath 'EFI');Attributes=[IO.FileAttributes]::ReparsePoint} }
    Microsoft.PowerShell.Management\Get-ChildItem -LiteralPath $LiteralPath -Force -ErrorAction Stop
}
function Get-Content {
    [CmdletBinding()]param([string]$LiteralPath,[switch]$Raw)
    if ($script:fileMode -eq 'content-denied') { throw (New-Object UnauthorizedAccessException 'Synthetic content denied') }
    Microsoft.PowerShell.Management\Get-Content -LiteralPath $LiteralPath -Raw -ErrorAction Stop
}
function Get-FileHash {
    [CmdletBinding()]param([string]$LiteralPath,[string]$Algorithm)
    if ($script:fileMode -eq 'hash-denied') { throw (New-Object UnauthorizedAccessException 'Synthetic hash read denied') }
    Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm -ErrorAction Stop
}
foreach ($mode in @('root-denied','listing-denied','content-denied')) {
    $script:fileMode=$mode
    Assert-Refused { Get-SteamOsBootContentBlockReason $esp 'esp' } ('filesystem-error-is-failclosed:'+ $mode) 'Synthetic'
}
foreach ($mode in @('root-reparse','child-reparse')) {
    $script:fileMode=$mode
    Assert-Check ((Get-SteamOsBootContentBlockReason $esp 'esp') -match 'reparse point') ('reparse-attribute-is-protected:'+ $mode)
}
$script:fileMode='hash-denied'
Assert-Refused { Get-SteamOsBootContentBlockReason $officialEsp 'esp' } 'unreadable-fallback-cannot-pass' 'Synthetic'
$script:fileMode='normal'
$report=Join-Path $run 'checks.json'
[ordered]@{
    passed=$true;checks=$checks.ToArray();count=$checks.Count;powershell_version=$PSVersionTable.PSVersion.ToString()
    module_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'steam-boot-inspection.ps1') -Algorithm SHA256).Hash
    test_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash
    scope='Synthetic native byte/path parsers; throwing-default device/native seams; mocked handle binding and ordinary tool-owned content fixtures. No disk queries, real volume opens/enumeration, mounts, deletion, registry or hardware acceptance.'
    compatibility='Named SteamOS roles support empty volumes, same-volume SteamOS or project fallback, and project menus with a separate Windows ESP. Foreign and ambiguous EFI loaders remain protected.'
} | ConvertTo-Json -Depth 5 | Microsoft.PowerShell.Management\Set-Content -LiteralPath $report -Encoding UTF8
$report
