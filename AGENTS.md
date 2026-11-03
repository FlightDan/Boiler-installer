# Boiler Installer collaboration rules

- This is the independent Boiler Installer root. Keep all supported brand/model records, build tools, source snapshots, and verification metadata under this root. Store bulk verification media and generated working data according to the RAID workspace policy below. `adaptations/` owns shared, vendor, and model material. Never import a sibling checkout or assume an old workspace exists.
- Preserve existing work. Old project directories may be archived after independent relocation acceptance, with backups outside the old workspace. Deleting the old workspace requires formal Boiler release, public-download, upgrade/recovery and required hardware acceptance; no active tool or recovery input may depend on it.
- The primary agent owns architecture, integration, and final verification. Delegate independent investigation or substantial-change review when it adds value. Use gpt-6.1-sol with high reasoning for complex architecture; otherwise use gpt-6-luna with max reasoning. Keep dependent edits/tests sequential and evaluate reviewer findings.
- Before adding complexity for a speculative race or security edge case, explain the concrete failure, likelihood, and cost, and obtain user approval.
- Preserve private unpublished performance research. The source-only public CPU baseline is fixed at `72f864e0498c10366493c74608c5ffed62cc99ca`. Never source CPU policy from a current experimental checkout. Unapproved research, private device captures/access records, credentials, private keys, local helpers, and `.local/` must not enter source exports or packages. Public model adaptation records belong in `adaptations/` with their evidence and acceptance limits.
- Use the explicit `scripts/source-files.json` source whitelist and a separate public checkout. Keep the existing public repository origin and provenance. No nested `.git` directories, links to sibling trees, or copied internal Git history.
- Read the applicable adaptation rules before changing vendor/model material. Maintain immutable source locks and licenses; regenerate hashes from final bytes using the relevant preparation tools. Never hand-edit a generated manifest to pretend a payload was rebuilt.
- Each functional change includes consistent installation, maintenance, options/metadata, runtime payloads, and recovery behavior. Record each applicable delivery gate as complete, not applicable with a reason, or blocked with remaining steps.
- Record changes and acceptance boundaries in dedicated release/migration records. README changes are limited to necessary version replacements unless the user authorizes other prose changes.
- Keep source tests, Windows build, installer integration, maintenance upgrade/recovery, artifact download verification, and hardware validation separate. Tests do not establish physical-device acceptance.
- Release artifacts use a new immutable version/build identity and an exact public source commit. Prepare and verify maintenance first, then the matching Windows installer. Do not replace a public attachment. Verify final published downloads, signatures, inventories, sizes, hashes, version ordering, and required upgrades before declaring release completion.
- GitHub Release installer attachments must be slim installers without bundled model/device packages, including retained-maintenance copies. Use `scripts/package-release-installer.py` to package and verify the actual slim ZIP before the separate release step. Full installers and separately requested model packages may be prepared for local testing; do not upload those local full installers or model packages as GitHub Release attachments. Keep authenticated model acquisition and the generic maintenance distribution separate from the slim installer.
- Signing identities and host configuration are local external inputs under `.local/` or a separately configured secure store. Never embed their values or absolute old-workspace paths in public sources. A required unavailable external signing input is a release blocker, not a reason to generate a replacement identity.

## Project boundaries and version sources

- `app/` is the Windows WPF application and privileged helper boundary. `guest/` owns isolated Linux preparation/deployment; `protocol/` validates authenticated indices and model packages; `maintenance/` owns retained upgrade/recovery. Generic `components/` must not acquire vendor EC or experimental CPU policy implicitly.
- `adaptations/common/` holds cross-vendor material; `adaptations/brands/<vendor>/common/` holds vendor-shared material; model-specific records, source and bridge tools live under that vendor/model. Keep sources, provenance, licenses, compatibility, installation/maintenance coverage and hardware limitations together.
- Installer integration version: `components/release.json`. Generic maintenance version: `maintenance/release.json`. Device package and plugin/runtime versions remain independent; do not infer one from the installer's display version. Build timestamps and exact source commits distinguish local candidates.
- A model bridge consumes explicitly supplied, hash-locked artifacts. Current input regeneration must use retained project-local sources and locks; historical import tools requiring an explicit external input are not the default rebuild path.
- Development material outside the public whitelist is still user work. Preserve or privately archive it; a whitelist is a publication boundary, not a deletion list.

## Interfaces and safety review

### 设备修复归属与 TOON 引用（用户明确约定）

- 按实际硬件设备复用的通用修复统一放在 `FlightDan/Boiler-devices/common/`，机型包通过 TOON 引用同一份资源；不得把通用设备修复绑定到某一掌机型号或复制到各机型实现中。`devices/` 保留设备注册、兼容性及发现记录的职责，不作为通用修复实现的另一套根目录。
- SSD 修复按设备类型、厂商和型号组织，GM7 的目标资源路径明确为 `common/ssd/acer/gm7/`；不再把 `device/`、`devices/` 与 `common/` 写成可选方案。品牌共用资源放 `brands/<vendor>/common/`，机型专属资源放对应品牌/机型目录。现有公开仓库尚无的资源路径应按此规则新增并建立 TOON 引用，不能声称已存在或已迁移完成。
- GM7 修复按目标 SSD 的实际身份匹配，不以 OXP3/X2 等掌机 DMI 代替 SSD 身份。机型 TOON 引用资源表示提供该修复，实际安装仍必须核验 SSD 匹配。
- 机型包已引用并提供兼容修复时优先使用该引用；否则匹配的设备修复进入“自定义构件”。匹配后默认勾选，不匹配或身份不明不安装；同一目标上用户明确取消的选择不得被刷新或提供方切换重新勾选。
- 安装器只负责资源解析、可信匹配、选择和通用事务执行；OXP3 专属适配及其身份校验保留在对应机型层。不得为了让 X2 复用 GM7 修复而冒充 OXP3、扩展 OXP3 专属身份白名单或绕过校验。
- 本轮额外拆分到 `common/` 的范围仅为 Intel xe VMA 修复、Intel BE201 固件、Realtek ALC245 扬声器路由和 BMI260 陀螺仪，另保留已经确定的 GM7 方案。灯效、按键、HDR、CPU 和电池不进入本轮迁移；设备公共实现与仍需机型配置的参数必须分开。
- “部件识别”是 Windows 安装器“自定义构件”页面的本机只读功能，通过 Windows 设备枚举读取当前电脑的 PCI/USB/HDAUDIO/ACPI 等真实标识，不是在目标硬盘安装识别程序，也不依赖 SSH。结果应展示设备标识、来源和匹配状态；不能把当前电脑的硬件当成外接目标盘所属掌机的硬件，也不能用 Windows 标识冒充已验证的 Linux 驱动、内核兼容性或 BMI260 芯片读数。识别不执行驱动绑定、模块加载、EC/I2C 写操作或磁盘部署。
- 若公开仓库目前仍只是 registry，或本地存在旧桥接实现，应明确记录资源层迁移并保留来源、锁、许可证和恢复语义；不得把旧结构限制当作继续把 SSD 修复塞进机型分支的理由。打包和更新 GitHub 前核对资源归属、两台机型的 TOON 引用及实际生成载荷。

- Keep existing index/package, plan, helper request and receipt formats compatible unless an approved migration is required. Explicit developer/local-test trust never becomes production trust by a filename or a self-declared digest.
- Validate the reviewed source, physical target identity and request binding through final use. Do not weaken protected-volume rules, leases, source-handle retention or recovery barriers to make a test pass.
- Guest paths resolve inside the guest image namespace. Host paths, symlink/junction ancestors, package hooks and archive members are separate trust boundaries.
- Every confirmed defect needs a reproduction and targeted regression. Stop and report uncertain ownership, changed disk/firmware state or unclean recovery; do not automatically overwrite it.

## Build and verification entry points

- Use the exact dependency locks and the existing dependency bootstrap/preparation scripts. Verify all required runtime members; the presence of a runtime directory alone is insufficient. Current local active model caches and their retained-maintenance copies must match.
- Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-app.ps1 -Configuration Release`, then run the built application or bundled dotnet host with `--cli boiler-checks`, `--cli model-catalog-checks` and suites appropriate to the change. The apphost has an administrator manifest; sandbox launch refusal is not a successful application check.
- Python: run `python3 -B -m unittest discover -s tests -p 'test_*.py'`. Run guest tests separately with the required `PYTHONPATH`. Windows skips of `fcntl`, FIFO, Unix permissions, symlinks and privileged hooks require genuine Linux verification.
- On controlled Linux, test maintenance as root and Opti as an ordinary user. Set `OXP_EXT4_REAL_FIXTURES=1` only after checking that its fixtures are temporary regular images. Model/adoption integration uses this project's adaptation bridge or an explicit `BOILER_MODEL_SOURCE` input.
- Test names are not safety guarantees: inspect commands before running them. Use synthetic data and tool-owned VM/VHD images. No user disk, ESP, firmware, physical device, suspend, reboot or installation action is implied by an ordinary source audit.
- Rebuild affected inventories through normal generators. Validate every final archive member against the inventory, source commit, size and SHA-256, including both Windows and retained-maintenance model copies. Re-extract and run the actual packaged executable and offline/cache checks.
- A screenshot with no rendered content is a blocked visual check, not visual acceptance. Keep source, mock/VM, artifact, actual UI and hardware results separate.

## Cleanup and deletion readiness

- Inventory before cleanup. Regenerable intermediates may be removed; preserve source changes, accepted artifacts, signing identities, input archives, manifests, transaction journals, recovery media and exact build/validation evidence. ACL-denied entries remain preserved and reported.
- Historical evidence is immutable. Update only current pointers, launchers and supported configuration, and record historical-to-current path mappings separately.
- Before declaring relocation complete, build and package from this root without sibling imports, junctions or implicit old-workspace discovery. Archive the latest deliverable and its proof locally; verify that release authentication and signing remain available from a secure location outside the old workspace.

## RAID workspace and C: disk-space policy

- On this machine, use the RAID volume (currently `E:`) and a dedicated project-owned directory for installer preparation, VM/VHD/raw images, cloning, extraction, staging, large build/package intermediates, and large test outputs. Do not default these operations to `C:` or the current checkout directory. Verify the volume identity and free space before starting.
- Preflight the full operation's source copies, expanded images, overlays, scratch space and temporary outputs, including concurrent work. If space is insufficient, select the RAID workspace before execution; do not retry on `C:`.
- Keep source and small verification records on `C:`. A final TAR may be placed at the C: root when explicitly requested. Do not keep accumulating full extracted packages, duplicate runtimes, or other regenerable bulk copies there. Record external media locations, ownership, sizes and SHA-256 hashes in project-local manifests; do not use junctions to conceal bulk data locations.
- Inventory before reclaiming space. Remove only verified regenerable intermediates that no active process or current validation depends on. Preserve accepted artifacts, source changes, signing inputs, original input archives, logs, journals, recovery media and exact acceptance evidence. Archive required bulk material to RAID and verify it before changing current references; retain historical path mappings.
- When handing over an installer for this machine, state that its preparation/work directory must be on the RAID volume; C: free space does not currently meet the complete preparation flow's requirements. Never ship this machine's absolute workspace setting inside the portable package.
