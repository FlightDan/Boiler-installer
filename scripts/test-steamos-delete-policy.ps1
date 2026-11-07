param([Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'steam-partition-layout.ps1')
. (Join-Path $PSScriptRoot 'partition-delete-policy.ps1')
$checks = New-Object 'System.Collections.Generic.List[string]'
function Assert-Check([bool]$Ok, [string]$Name) { if (-not $Ok) { throw "Failed: $Name" }; $checks.Add($Name) }
# Every environmental/native entry is replaced; these checks cannot touch a disk.
function Get-Disk { throw 'Real disks forbidden' }
function Get-Partition { throw 'Real partitions forbidden' }
function Remove-Partition { throw 'Real deletion forbidden' }
function Test-UnrecognizedPartitionAttributes { throw 'Unexpected generic native probe' }
function Get-SteamOsDeletionMetadata { $script:native }
function Get-SteamOsBootRoot { if ($script:rootFailure) { throw 'Unbound volume' }; '\\?\Volume{11111111-1111-4111-8111-111111111111}\' }
function Test-SteamOsBootHostPath { $script:hostHit }
function Get-SteamOsBootContentBlockReason { $script:contentReason }
function Get-CimInstance { @() }
function Get-ItemProperty { [pscustomobject]@{ DumpFile = 'C:\Windows\MEMORY.DMP' } }
function Test-Path { $true }
$disk = [pscustomobject]@{ Number=9999; Guid='22222222-2222-4222-8222-222222222222'; Size=128GB; PartitionStyle='GPT'; BusType='NVMe'; LogicalSectorSize=512; IsOffline=$false; IsReadOnly=$false }
$part = [pscustomobject]@{ Guid='33333333-3333-4333-8333-333333333333'; PartitionNumber=7; Offset=1MB; Size=256MB; GptType='c12a7328-f81f-11d2-ba4b-00a0c93ec93b'; Type='System'; IsBoot=$false; IsSystem=$false; IsOffline=$false; IsReadOnly=$false; IsHidden=$false; AccessPaths=@(); DriveLetter='' }
$volume = [pscustomobject]@{ FileSystemType='FAT32'; HealthStatus='Healthy'; Path='' }
$evidence = [pscustomobject]@{ guid=$part.Guid; offset=$part.Offset; size=$part.Size; gptType=$part.GptType; gptName='esp'; gptAttributes='0'; nativeGptVerified=$true }
$script:native = [pscustomobject]@{ Id=[guid]$part.Guid; Type=[guid]$part.GptType; Offset=$part.Offset; Size=$part.Size; Name='esp'; Attributes='0' }
$script:rootFailure=$false; $script:hostHit=$false; $script:contentReason=''
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -eq '') 'steamos-esp-is-deletable-after-native-and-host-checks'
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume) -ne '') 'ordinary-efi-remains-protected'
foreach ($key in @('IsBoot','IsSystem','IsOffline','IsReadOnly')) {
    $part.$key=$true
    Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -ne '') ('hard-protection:'+ $key)
    $part.$key=$false
}
$script:native.Attributes='1'
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -ne '') 'native-required-attribute-protected'
$script:native.Attributes='0'; $script:native.Name='foreign'
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -ne '') 'native-name-replacement-refused'
$script:native.Name='esp'; $script:hostHit=$true
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -ne '') 'host-volume-protected'
$script:hostHit=$false; $script:rootFailure=$true
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -ne '') 'missing-verifiable-volume-protected'
$script:rootFailure=$false; $script:contentReason='Windows or shared boot files are protected.'
Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -ne '') 'shared-windows-boot-protected'
$script:contentReason=''
foreach ($role in @('efi-A','efi-B')) {
    $part.GptType='ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'; $part.Type='Basic'; $part.Size=64MB; $part.IsHidden=$true; $part.IsReadOnly=$null
    $evidence.gptType=$part.GptType; $evidence.gptName=$role; $evidence.size=$part.Size
    $script:native.Type=[guid]$part.GptType; $script:native.Name=$role; $script:native.Size=$part.Size
    Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume $evidence) -eq '') ($role+'-hidden-with-zero-native-attributes-deletable')
    Assert-Check ((Get-PartitionDeletionBlockReason $disk $part $volume) -ne '') ($role+'-without-reviewed-role-stays-protected')
}
# Exercise the REAL final target rebind too, not only the orchestration stub.
. (Join-Path $PSScriptRoot 'partition-delete-snapshot.ps1')
function Get-Disk { [CmdletBinding()]param([int]$Number); return $script:disk }
function Get-Partition { [CmdletBinding()]param([int]$DiskNumber,[int]$PartitionNumber); return $script:part }
function Get-Volume { [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$InputObject); process { return $script:volume } }
$target=[pscustomobject]@{guid=$part.Guid;offset=$part.Offset;size=$part.Size;gptType=$part.GptType;number=$part.PartitionNumber;deletionAllowed=$false}
$fresh=[pscustomobject]@{guid=$disk.Guid;size=$disk.Size;partitions=@($target)}
Assert-Check ((Get-ReviewedDeletionTarget $disk.Number $fresh $target $evidence).Guid -eq $part.Guid) 'final-target-rechecks-bound-boot-context-despite-lost-group-allow'
$script:hostHit=$true
$refused=$false; try { $null=Get-ReviewedDeletionTarget $disk.Number $fresh $target $evidence } catch { $refused=$true }
Assert-Check $refused 'final-target-current-host-protection-overrides-old-context'
$script:hostHit=$false
$refused=$false; try { $null=Get-ReviewedDeletionTarget $disk.Number $fresh $target } catch { $refused=$true }
Assert-Check $refused 'final-target-without-context-cannot-bypass-fresh-policy-denial'
$part.Offset+=1MB
$refused=$false; try { $null=Get-ReviewedDeletionTarget $disk.Number $fresh $target $evidence } catch { $refused=$true }
Assert-Check $refused 'final-target-device-range-replacement-refused'
$part.Offset-=1MB
$null=New-Item -ItemType Directory -Path $ReportDirectory -Force
[ordered]@{ passed=$true; checks=@($checks.ToArray()); scope='Actual deletion policy with synthetic objects and mocked native/host/content I/O; no disk access.' } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReportDirectory 'checks.json') -Encoding UTF8
$checks
