# 真实安装会话夹具与 VM 测试改进（2026-11-07）

## 范围与证据边界

输入是用户提供的两份已完成写入、停在 `awaiting-device-boot` 的安装日志导出：

- `1105`：外部盘（Realtek RTL9210，空白 1 TB），导入预制镜像，无设备组件。
- `1106`：内置盘（Predator GM7 1 TB，已有 6 个分区），`device:x2-mini` 开发者设备包，8 个组件 × 2 槽，SSD 修复，rEFInd，固件默认启动项。

本记录只证明：源码测试、Windows Release 构建、回放检查，以及在工具自有 QEMU 夹具上的部署和模型门控。它不证明真机、固件、硬件或视觉验收。

## 真实日志中观察到的事实

| 项目 | 1105 外部 | 1106 内置 |
|---|---|---|
| 目标盘 | Disk 1，RTL9210 USB，1,024,209,543,168 字节，无分区 | Disk 0，GM7 NVMe，既有 6 个分区 |
| 目标区间 | 起点 886,769,188,864，128 GiB，右对齐 | 起点 779,415,977,984，128 GiB，位于 D: 与 RecoveryImages 之间 |
| 角色编号 | 1–8 | 7–14 |
| 尾部保留分区 | 无 | RecoveryImages、RECOVERY，位于区间之后 |
| home 实际复制字节 | 775,274,496 | 2,409,873,408（GPT 大小 111,266,496,512） |
| 组件阶段 | 无 | 02.01 从 09:25:15.692 到 09:45:57.951，间隔 1242.26 秒，期间无进度 |
| 校验步骤 | 无 | 只有 02.03，没有 02.04 |
| 日志粘连 | 路径日志缺少换行 | `DESKTOP_STEAMOS_SCRIPT=` 与清理消息出现在同一行 |

物理写入的 `helper.jsonl` 中有 `PARTITION_PROGRESS`，没有 `PARTITION_DEPLOYED`。因此完成判定只能从最后一条 `verifying` 记录推出。

## 修复的缺陷

每项都有回归测试。

1. **OXP3 机型路径两槽都完成后才输出组件结果。** 现在每槽输出自己的结果。SSD 选择的计划延后到 `complete_ssd` 之后输出，因为 `ssd_repair` 只在那之后写入，导出时依赖它拒绝绑定目标的镜像。回归：`tests/test_integration_emit.py`（3 项）。
2. **生产 `integration-verify` 只输出 `OXP_INTEGRATION_VERIFIED`，进度跟踪器只认 `OXP_INTEGRATION_VERIFY` JSON。** 现在两种标记都输出，跟踪器保持 JSON 契约。回归：`tests/test_guest_marker_contract.py`（3 项），以及 `installation-progress-checks` 中的 `late-slot-a-row-does-not-regress-verified-step`。
3. **SteamOS 脚本路径日志缺少换行。** 三条记录现在都以换行结束。回归：`desktop-reboot-checks` 中的 `script-path-log-lines-are-separate`，以及 `tests/test_guest_marker_contract.py`。

## 固定夹具

`tests/fixtures/install-sessions/1105-external` 与 `1106-internal`，由 `scripts/sanitize-install-session.py` 从两份导出生成：

- `manifest.json`：每个文件的大小与 SHA-256，以及脱敏规则和采样策略。
- `partition-layout.json`：布局，含保留分区、角色、选择区间和 `home_used_bytes`。
- `full-transcript.log`：只保留分区与物理标记行。
- `progress.jsonl`：过滤后的进度记录，保留顺序和百分比（每步切换、每步首末、每 50 条一条）。
- `disk-snapshots.json`、`partition-plan.json`、`physical-state.json`、`session.json`。
- `verify-markers.log`：由真实 `OXP_INTEGRATION_VERIFIED=A/B` 行转换为 `OXP_INTEGRATION_VERIFY` JSON 格式。1105 没有对应行，因此为空。
- 1106 另有 `components.json`（18 行）、`install-plan-A/B.json` 和固件的 plan/before/result。

`tests/fixtures/disks/` 下有三份 read-disks 形状的快照：`1105-session-disks.json`、`1106-gm7-internal.json`（6 个既有分区）、`1106-gm7-after-install.json`（6 个保留分区加角色 7–14，用于识别与删除策略）。快照由 `scripts/build-fixture-disk-snapshots.py` 生成。

脱敏规则：确定性 GUID、同形序列号、用户与安装路径替换、32 位 nonce 替换。GPT 类型 GUID 保留，它们是公开常量，用于角色识别。`tests/test_install_session_fixtures.py` 检查 manifest 哈希、拓扑、进度单调性、步骤序列，以及是否残留个人路径和真实序列号。

## 回放检查

`app/InstallationSessionReplayChecks.cs`，由 `--cli install-session-checks` 调用，`boiler-checks` 也会调用（名称 `real-install-session-fixtures-replayed`）：

- 校验 manifest 哈希。
- 用真实 `PARTITION_PROGRESS` 行回放，不要求 `PARTITION_DEPLOYED`。
- 8 个角色最终满足 `written == verified == size`；`home` 总字节等于 `home_used_bytes`；百分比单调。
- `progress.jsonl` 的百分比单调，最终不低于 99。步骤按实际生命周期顺序检查，不按数字排序：`03.01` 后回到 `01.06/01.07` 是历史生产流程。新增的顺序断言尚未重新运行。
- 验证标记经 `InstallationProgressTracker` 推进到 02.04 和 75%。
- 磁盘快照的形状。

本次补写 `app/InstallSessionFixtureChecks.cs`，由会话回放调用：使用生产的 `DiskPlanning.FreeSpaces`、`PartitionSelection.Target()` 和 `PhysicalInstallation.Plan`，断言两个真实空隙、128 GiB 右对齐起点 `861000433664` / `886769188864`、角色编号 7–14 / 1–8、全部角色的偏移和大小、两个尾部保留分区不重叠；通过生产 `SteamOsPartitions.Detect` 与 `PartitionInventory.Read` 识别 after-install 快照，并检查只读、4096 字节扇区和错误角色类型的拒绝。**新增代码尚未构建或执行，不能计入已通过结果。**

同批补写：manifest 的完整成员集合、大小与哈希校验；按生产 AST 实际执行的 mock 双槽编排测试（普通、延迟 SSD、B 槽失败不输出最终 SSD 计划）；进度时长按下一步骤边界计算，另保留记录跨度、采样记录间隔和末尾证据不完整标记。`01.07` 的 UTC 跨度是 27.112582 秒，独立取整的 `elapsedMs` 差为 27113 ms，测试使用后者。以上均待命令执行恢复后验证。

## 生产 VM 自检盘拓扑

- `guest/fixture-layout.json` 与 `guest/fixture_layout.py` 是唯一事实来源。Profile：
  - `production`：6 个前置保留分区，区间 1 GiB 起、64 GiB，角色 7–14。这是 `CreateFixtureAsync` 的默认值，也是每次非 prepared 安装的 01.03/01.04 自检盘。
  - `external`：空白盘，区间 1 MiB 起，角色 1–8。
  - `seed`、`legacy`：分别为 2 个保留分区（`legacy` 的角色为 3–10）。
- `Backend.CreateFixtureAsync(log, token, profile)`：`InstallationController` 按安装类型选择 `production` 或 `external`；`virtual-create [profile]` 可显式指定。
- guest 脚本按名称解析角色和保留分区，不再硬编码 `/dev/vdd` 编号。涉及 `partition-test.sh`、`handoff-metadata.sh`、`deployment-fixture.py`、`deployment-hardstop.py`、`intent-hardstop.py`、`journal-migration-check.py`、`ui-installed-check.py`、`ui-cancelled-check.py`、`firstboot-prepare.sh`、`firstboot-verify.sh`、`hdr-test.sh`、`plugin-test.sh`、`shared-state-test.sh`、`shared-state-test.py`、`ui-bootstrap-seed.sh`。
- 生产 `prepared.img` 的约束没有改变：保留分区全部位于角色区间之前，8 个角色是最后 8 个分区，`home` 最后（见 `app/PartitionDeployment.cs` 和 `guest/resize_prepared.py`）。
- `tests/test_fixture_layout.py`（5 项）检查各 profile 的名称、编号、区间，以及 profile 检测。

未完成：`vm-internal`（尾部保留分区的 QEMU 专用拓扑）没有实现。真实 1106 的尾部保留分区只在物理写入路径上验证过，即那次真实安装本身，不是 VM 测试。Windows 侧的 VHD 紧凑夹具也未修改。

## VM 诊断基线

- `scripts/check-vm-stderr.py` 与 `tests/fixtures/vm-stderr-allowlist.json`：只检查带失败特征的行（`error`、`fail`、`No such file`、`mismatch` 等）。按任务名或 `*` 放行，每条有计数上限和理由；未列入即失败。
- `tests/test_vm_stderr_allowlist.py`（10 项）。
- 尚未接入自动运行器。当前是可独立调用的分类工具，还不会在每次 VM 任务后自动运行。

## 门控结果

工作副本：`E:\Boiler-workspace\fixture-tests-20261107-test`，仅用于测试，分离头指针。Release 构建与 OXP3 门控对应 `132175e`；随后同步到 `bbb54ff` 运行 Python 全套与外部部署。运行时与缓存是从主检出复制的，不使用链接；未复制 `.local/`。

| 门控 | 实际结果 / 证据 |
|---|---|
| Windows Release 构建 | `132175e`，0 警告、0 错误；不是本次未提交修改的构建 |
| `boiler-checks` | `work/gate-boiler-checks-result.txt`：通过，包含真实会话回放 |
| OXP3 机型 VM | `work/gate-model-oxp3-result.txt`：`passed=true`、`original_fixture_unchanged=true`，14 条组件结果；A/B legacy 产物各 2085 文件、43 journals；两槽独立只读校验和 physical-readiness 通过 |
| OXP3 源包与输入 | `device:oxp3` 2026.11.5.2，manifest SHA-256 `f6641d0f82db2cca02114a371410061ec54fb3ef79d7053a6c63ce8eabe4a13a`；显式 developer-session，不是生产签名信任 |
| OXP3 原始夹具 | SHA-256 `3eff8361116ab6458abcae3aaccd86a1f9d7a60f4ba03ce3512da793482f9788`；结果报告确认原夹具未变 |
| OXP3 校验标记 | `boiler-model-f282ca616170462b9fa5543653e26c87/verification-output.log` 中 A/B 均有 human 与 JSON 双标记；本次没有选择 SSD，不能当作 SSD 延迟计划路径验收 |
| Python 全套 | `bbb54ff`：983 tests，439.175 秒，OK，skipped=96；证据 `work/suite-final.txt`；Windows 跳过项仍需真正 Linux 验证 |
| 外部空白 VM 部署 | `virtual-e8c5670bedd34f54aee9ebdd7cf771af/windows-fixture.qcow2`；`OXP_DEPLOY_TEST=journal-retry-roots-and-home-preserved`、`external-blank-fixture-no-preserved-partitions`、`OXP_TASK_RESULT=passed`；286.055 秒；证据 `work/gate-external-deploy-result.txt` |
| stderr 分类 | **未通过**，见 `work/stderr-scan.txt`。发现词内匹配 `invalidation`、JSON 文件名/描述、成功的拒绝测试名等误报，以及待分类的 TSC 校准诊断；未扩大放行表、未接入强制自动门控 |
| 本次新增断言 | **未执行**：会话命令安全检查服务持续限流，PowerShell 与调查子代理未能启动；文件工具可用。不能用上述旧提交结果覆盖新增工作区修改 |

VM 大文件均位于 `E:\Boiler-workspace\timing-20261106\work`。原始日志 ZIP 保持不变。当前未打包、未发布、未推送分支；夹具隐私还需核对固件 entry 的二进制身份与跨表示 GUID 一致性，并通过正常生成器重新生成、与私有原输入比对。前序提交曾把私有匹配值写入测试源码，本次移除了这些字面量，但删除不能消除 Git 历史中的值；不得直接公开这条历史。

## 交付门

- 安装：工具自有 VM 上的 `production` 与 `external` 部署门控。见上方门控结果。
- 维护：未改动维护实现；完整维护升级/恢复验收未执行。
- 恢复：现有 deployment-test 的 configure 故障与重试已通过；批准计划中的 format、copy-roots、SSD 后等新增边界**尚未完成**，不得记成不适用。
- 选项与元数据：进度和标记契约已改，见上方回归测试。
- 运行时载荷：新增 guest 文件 `fixture-layout.json`、`fixture_layout.py`、`integration_emit.py`，已登记到 `scripts/source-files.json`。
- 真机与硬件：未执行，未声称。

## 验收边界

- 本轮的 VM 结果只来自工具自有 80 GiB QCOW2 夹具，不是真实磁盘。
- 回放检查只说明真实日志被进度跟踪器正确解释；不证明 SteamOS 能启动。
- 诊断放行表是已观察项的记录，不是验收声明。
