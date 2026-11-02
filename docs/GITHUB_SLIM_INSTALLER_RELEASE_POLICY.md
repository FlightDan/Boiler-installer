# GitHub installer asset policy

The Windows installer ZIP attached to a GitHub release must be the slim
installer candidate named `boiler-installer-<version>-win-x64-slim.zip`. It
must not contain bundled device model package bytes. The pinned model catalog
snapshot stays in the installer so the UI can display supported models; the
model packages themselves use their separate signed distribution flow.

## Build and verify a candidate

Build from a clean independent public checkout at the exact source commit. Put
the artifact and its working files on the configured RAID workspace, outside
the checkout. Run the script from that same checkout so the executing builder
and protocol code belong to the source commit being packaged:

```powershell
$artifact = 'E:\Boiler-VM-Audit-<run>\artifacts\boiler-installer-<version>-win-x64-slim.zip'
Push-Location <clean-public-checkout>
& .\runtime\python\python.exe -B .\scripts\package-release-installer.py package `
  --root . `
  --source-commit <40-character-public-HEAD> `
  --output $artifact

& .\runtime\python\python.exe -B .\scripts\package-release-installer.py verify `
  --archive $artifact `
  --source-commit <same-public-HEAD>
Pop-Location
```

The packaging command delegates to the existing installer packager with
`local_test=False`, checks the public checkout and source commit, and verifies
the finished ZIP inventory. Verification requires the existing `preview`
package channel, `source_published: true`, the slim artifact filename, the
catalog display metadata and generic maintenance runtime. It rejects local-test
metadata, top-level and retained-maintenance model bundle paths, and any
`local_test_models` integration marker. It does not add a channel or change an
existing manifest schema.

The existing `--local-test` packaging route remains for local full-installer
testing. Its output is not a GitHub release asset. Device package ZIPs and the
generic maintenance release continue through their existing independent
distribution and signing procedures.

## Acceptance boundary

The `verify` command checks the candidate ZIP's inventory, metadata and member
hashes. Its result is not a signature check, an upload, or publication
authorization. It does not claim the release's separate source, signing,
download, upgrade, recovery or hardware gates. A future upload step must attach
only the verified slim installer ZIP as the Windows installer asset and must
complete the existing release gates independently. This script has no GitHub
credentials, does not access the network, and does not publish anything.
