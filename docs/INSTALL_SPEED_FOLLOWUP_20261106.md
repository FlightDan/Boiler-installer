# 首次安装提速后续（2026-11-06 起）

本记录接续 [INSTALL_SPEED_20261106](INSTALL_SPEED_20261106.md) 的“尚未完成”四项：
WHPX、apk 只读镜像、A/B 两槽并行、三组计时对比与真机首次安装验收。原记录作为历史保持不变，
本文件记录后续改动、交付门和验收边界。

## 阶段 1：计时聚合与回放

### 已实现

- `scripts/summarize-install-timing.py`：解析安装日志中的 `BOILER_TIMING`、`BOILER_HOST_TIMING` 和
  `BOILER_HOST_RESOURCES`，把每个脚本起点起的累计值换算为阶段时长，并支持多份日志对比
  （`--json`、`--top`、`--progress`）。它只读日志，不参与任何安装判定。
  - 同一 `scope` 每次客户机任务都会重新计时；解析器以“首个阶段名重复”或“数值回退”识别新的一次运行，
    因此同一次安装里多次 `common.sh` 的 `apk`/`modules` 不会被错误累加。
- `app/Backend.cs`：失败、超时和取消路径也输出 `BOILER_HOST_TIMING=<task>:vm-exit:<秒>:status=...`；
  成功路径保持原有 `vm-exit:<秒>` 格式。此前失败样本没有结束时间，无法纳入对比。
- `components/integration.py` 的 `stage()`：新增 `BOILER_TIMING=stage-<槽>:<阶段>:<秒>`，覆盖
  `validated`、`device-plan`、`payload-hash`、`validation`、`model-preflight`、`core-maintenance`、
  `firmware`、`device-components`、`ssd`、`decky`、`presets`、`retained`、`total`，以及
  `preserved`、`no-components` 两个提前返回路径。所有值相对 `stage()` 起点。
- `guest/integration-image.py`：通用路径新增 `slot-<槽>`（进入槽循环）与 `mount-<槽>`（挂载完成），
  与既有 `stage-<槽>` 组成“挂载 / 安装”两段；机型 EFI 路径新增 `password`、`stage-<槽>-start`、
  `ssd-complete`；无组件探测路径新增 `probe-<槽>`。
- `guest/integration-verify.py`：每个槽新增 `slot-<槽>`、`mount-<槽>`，与既有 `verified-<槽>` 组成复核耗时。
- 单元测试 `tests/test_install_timing.py`：覆盖累计值换算、串口 CR/ANSI 噪声、多次客户机运行、
  失败/超时状态、缺失 `vm-exit` 告警、进度 JSONL 和多日志对比。

### 阶段标签对应

| 日志标签 | 含义 | 出处 |
|---|---|---|
| 02.01 / 02.02 | A / B 槽组件安装 | `app/InstallationController.cs:263`、`app/InstallationProgressTracker.cs:98` |
| 02.03 / 02.04 | A / B 槽独立复核 | `app/InstallationController.cs:322`、`app/InstallationProgressTracker.cs:92` |
| 01.06 | 容量调整 | `app/InstallationController.cs:343` |

原记录中的“02.01 组件虚拟机 1243 秒”对应整个 `integration` 客户机任务；进度日志按完成记录把同一任务
拆成 02.01 和 02.02 两步。因此对比时应同时看 `BOILER_HOST_TIMING=integration:vm-exit` 和本记录新增的
`stage-A` / `stage-B` 阶段值，不能把进度步骤耗时直接当作客户机运行时长。

### 验收边界

- 源测试：`tests/test_install_timing.py` 9 项通过；完整套件 `runtime/python/python.exe -B -m unittest discover -s tests -p 'test_*.py'`
  为 956 项通过、96 项跳过（Windows 跳过的 `fcntl`/FIFO/Unix 权限/符号链接/特权钩子仍需真实 Linux 验证）；
  `test_guest_integration.py` 30 项、`test_firmware_integration.py` 5 项、`test_system_presets.py` 14 项通过；
  `test_ssd_integration_preflight.py` 1 项通过（需将 `components/` 加入 `sys.path`）。
- 本阶段仅增加日志字段，没有改变任何安装判定；阶段 5 已用真实 QEMU 客户机日志验证对比脚本。

## 阶段 2：WHPX 加速

### runtime 不重建

锁定的 `runtime/qemu-min/qemu-system-x86_64.exe`（QEMU 11.1.0，`v11.1.0-12130-ge470268ff4`，
SHA-256 `47d57a60…b3cd`）已通过 `-accel help` 报告 `tcg` 与 `whpx`，因此本轮不重建运行时、
不修改 `scripts/runtime-dependencies.lock.json` 的 QEMU 成员和归档哈希。`runtime/qemu-min/bundle-report.json`
与 `scripts/bundle-qemu.mjs` 中的作用域说明仍是生成时的旧描述（“WHPX … pending”）；该报告由已不在
检出中的 `runtime/qemu-full` 生成，不能重新生成，按生成物规则不手改。`docs/DEPENDENCY_BOOTSTRAP.md`
已更新为“锁定二进制已声明 WHPX，WHPX 是否可用需真实启动验证”。

### 已实现

- `app/GuestResources.cs`：
  - `GuestResourcesPolicy.Allocate()` 增加纯函数参数 `whpxAvailable` 与 `forcedAccel`，返回 `AccelAttempts`：
    自动模式下为 `whpx → whpx,kernel-irqchip=off → tcg,thread=multi`；宿主不支持 WHPX 时只用 TCG。
  - `ForHost()` 仍是唯一实时读取点：用 `WinHvPlatform.dll!WHvGetCapability(WHV_CAPABILITY_CODE_HYPERVISOR_PRESENT)`
    探测；DLL 缺失、功能未启用或调用失败都返回不可用。
  - 环境变量 `BOILER_QEMU_ACCEL=tcg|whpx` 用于计时对比时强制加速器；强制值按原样使用，不会静默回退，
    未知值被忽略。
- `app/Backend.cs`：QEMU 启动按 `AccelAttempts` 逐个尝试。只有“客户机尚未到达登录提示（phase 0）”的失败
  才进入下一次尝试，此时任务命令从未发送、目标字节未被写入；超时、用户取消或已有客户机输出时立即失败，
  不重复执行。每次回退记录 `BOILER_HOST_RESOURCES=<task>:accel-fallback:<from>-><to>:no-guest-output`，
  回退尝试使用独立日志 `<id>-accel1.txt`。失败、超时、取消路径均记录 `vm-exit`。
- `app/VirtualBoot.cs` 使用同一策略选择加速器；它是 UEFI 验证工具而非首次安装路径，本轮未加入逐次回退。
- 检查：`GuestResourcesChecks` 覆盖默认 TCG、WHPX 阶梯、强制覆盖和未知值；`--cli boiler-checks`
  新增 `qemu-runtime-advertises-whpx-and-tcg`。

### 本机探测结果

- `runtime/qemu-min/qemu-system-x86_64.exe -accel help` 列出 `tcg`、`whpx`。
- 实际以 `-accel whpx -machine q35` 启动客户机：`WHPX: No accelerator found`，QEMU 退出码 1。
- `dism /english /online /get-featureinfo /featurename:HypervisorPlatform` 为 `State : Disabled`；
  `Win32_Processor` 报告 `VirtualizationFirmwareEnabled=False`、`SecondLevelAddressTranslationExtensions=False`、
  `VMMonitorModeExtensions=False`；`HypervisorPresent=True`，本机本身运行在虚拟化环境中。
- 结论：启用 WHPX 需要宿主机暴露嵌套虚拟化并启用 Windows 功能、重启，属于主机侧变更，本轮未执行；
  **本机无法做 WHPX 性能对比或 WHPX 真机验收**。支持 WHPX 的目标机将自动选用 WHPX。

### 交付门

- 安装：完成（宿主启动参数与回退）。
- 维护、恢复：不适用，设备上的维护与恢复在 Linux 中运行，不经过宿主 QEMU。
- 选项/元数据：不适用，只有会话级环境变量，没有界面或包清单变化。
- 运行时载荷：不适用，QEMU 二进制、依赖锁与归档哈希均未变化。

### 验收边界

- Windows Release 构建成功（0 错误）。
- `--cli boiler-checks` 通过（`passed: true`），结果包含新增的
  `qemu-runtime-advertises-whpx-and-tcg`、`payload-volumes-mark-packages-already-installed`
  和 5 项加速器策略检查；`visual_acceptance=captures-produced-needs-review`，仍需人工查看截图，
  不构成视觉验收。最终结果另存于 `E:\Boiler-workspace\timing-20261106\boiler-checks-final-20261106.txt`。
- 本机未成功运行 WHPX 客户机；WHPX 加速收益和真机兼容性仍需在有 WHPX 的目标硬件上验收。

## 阶段 3：apk 只读镜像（量化未完成，镜像未实施）

### 已核实的现状

- 离线包是 **48 个**，不是原记录写的 47 个：`scripts/runtime-dependencies.lock.json` 的
  `offline_packages` 与 `tests/test_dependency_bootstrap.py` 都按 48 个断言；其中 5 个
  （bash、findmnt、lsblk、libmount、blkid）是单独的 raw 归档。
- `guest/common.sh` 每次客户机任务都执行 `apk add`，然后才 `modprobe btrfs/ext4`；
  共 41 个客户机任务 source 该脚本。
- 多卷载荷路径 `app/PayloadVolumes.cs` 在组装 `/mnt/payload` 前已经用 volume 0 的 apk
  安装过一次（`mkfs.ext4` 需要 e2fsprogs），`common.sh` 随后又安装同一批包。

### 已实现

- `app/PayloadVolumes.cs`：多卷挂载命令在 `apk add` 成功后写入
  `/var/cache/apk/.boiler-apk-prepared`。
- `guest/common.sh`：仅当该标记不存在时才执行 `apk add`；计时行仍在守卫外，
  跳过的安装会显示为接近 0 的 `common:apk`，而不是缺失记录。
- 回归：`app/BoilerChecks.cs` 新增 `payload-volumes-mark-packages-already-installed`；
  `tests/test_guest_apk_preparation.py` 断言脚本只出现一次 `apk add` 且位于标记守卫内，
  并检查多卷命令只在安装成功后写标记。

### 决策门

本机没有可用的 WHPX，也没有完成一次可比的 X2 Mini 首次安装，无法得到 `common:apk` 在首次安装中的
总占比。按计划门槛（占首次安装 2% 或至少 30 秒）暂不实施只读镜像；需要阶段 5 的 E: RAID 计时数据
再决定。若达到门槛，构建方案仍按计划：工具自有客户机 VM 内 `apk add --root` 到暂存根后生成 squashfs，
固定 `SOURCE_DATE_EPOCH` 与压缩参数，镜像哈希进入依赖锁并由宿主在挂载前复核，客户机以只读 raw 磁盘
挂载并 overlay 合并，不静默回退到 `apk add`。

### 实际观察（2026-11-06，TCG）

- 单卷部署任务（`deployment-test`，载荷小于 400 MiB）仍执行安装：`BOILER_TIMING=common:apk:3.720`。
- 多卷 X2 Mini 集成任务由挂载命令先安装同一批锁定包，`common.sh` 跳过重复安装：
  `BOILER_TIMING=common:apk:0.040`；同一次启动的 `common:modules` 为 1.400 秒。
- 以上是自动化 VM 日志，不是硬件验收。镜像方案的收益仍需阶段 5 的完整安装计时。

## 阶段 4：A/B 两槽并行（未实施，计时未达门槛）

已核实的串行依赖（与计划一致）：

- 通用路径两个槽共用 `/mnt/integration-root`，必须串行挂载。
- 全局 `/run/boiler-install-slot` 被设备钩子和 `maintenance/model_efi.py` 的 `_verified_parent` 读取。
- 机型 EFI 的 `_verify_fresh_sibling` 要求 A 槽的 `model-esp-*.json` 已 `installed` 且 adapter 记录为
  `applied`；`pair.json` 与单一 `model-pending.json` 位于共享 home。
- `boot-ownership-v1`、`ssd-boot-v1`、`keyboard-v1`、Decky 共享 marker、`maintenance-blobs`、
  `core-retention` 都位于共享 home；Engine 锁与事务日志在各槽自己的 `var` 分区。

结论：2026-11-06 的首轮 X2 Mini VM 计时（TCG）显示，每槽可并行前置工作（`device-plan` + `payload-hash`
+ `validation` 等）合计约 32 秒，占 `integration` 客户机 865.261 秒的约 4%，远低于 20% 门槛；
`core-maintenance` 单槽约 390/334 秒，是主要耗时。未实施并行，也未改变任何槽顺序、恢复或收据语义。
后续若实施，只并行只读/本槽分区步骤，所有写共享 home 的步骤保持 A→B 串行。

## 阶段 5：计时对比与真机验收

### 已完成的准备与首轮 VM 计时（2026-11-06，TCG）

- 工作区：`E:\Boiler-workspace\timing-20261106\work`（E: RAID，项目专用目录）；源镜像
  `E:\Boiler-installer\gm7-dropin-20261104\guest-lifecycle\recovery-3.10.img`（已存在，只读使用）。
- `--cli virtual-create` 创建 80 GiB 一次性 QCOW2 fixture；`--cli virtual-deploy` 部署 SteamOS 3.10
  （`deployment-test` 客户机 341.844 秒）。
- `--cli boiler-model-checks <fixture> cache/model-bundles/packages/x2-mini` 使用与向导相同的默认选择
  （X2 Mini 当前包为 `x2-mini:battery`、`x2-mini:microphone`，无 legacy adaptation）。
  `BoilerImageBuildChecks.ModelAsync` 已扩展：无 `oxp3:legacy-adaptation` 的包改用通用只读设备校验
  （每个槽逐组件 `BOILER_DEVICE_VERIFIED`），compatibility 有 `kernels` 时按内核、无 `kernels` 时按
  `os_versions` 和固定 3.10 校验；OXP3 分支原有断言不变。
- 首轮宿主计时：`integration` 865.261 秒（`stage-A` 423.141、`stage-B` 356.228；
  `stage-A:core-maintenance` 390.118、`stage-B:core-maintenance` 333.516；
  `payload-hash` 15.867/14.211、`device-plan` 9.380/0.276、`device-components` 7.217/7.635）；
  `integration-verify` 183.980 秒（A 51.038、B 48.682）；`physical-readiness` 56.090 秒。
- 首轮在功能步骤全部通过后，仅在“X2 包缺少 `compatibility.kernels`”这一 QA 断言处失败；已把该断言
  改为：有 `kernels` 时按内核校验，无 `kernels` 时按 `os_versions` 与固定 3.10 校验。失败不改变上述
  计时样本的有效性，但该样本不构成一次通过的模型检查。
- 证据：`E:\Boiler-workspace\timing-20261106\run1\`（3 个客户机日志、failure/progress/selection）
  与 `run1-summary.json`；部署证据为 `deployment-20261106.txt` 和
  `deployment-20261106-result.txt`。

### 第二轮通过回放（2026-11-06，TCG）

- 同一 fixture、同一 X2 Mini 选择；`boiler-model-checks` 退出码 0，`passed: true`，
  `original_fixture_unchanged: true`，`requires_legacy_adaptation: false`。
- 宿主计时：`integration` 875.373 秒（`stage-A` 430.526、`stage-B` 356.550；
  `core-maintenance` 396.723/333.792；`payload-hash` 17.400/14.662）；
  `integration-verify` 184.940 秒；`physical-readiness` 55.294 秒。
- 与首轮差异：`integration` +1.2%、`integration-verify` +0.5%、`physical-readiness` -1.4%，
  `stage-A` +1.7%、`stage-B` +0.1%；两轮重复性在约 2% 以内。两轮均为 TCG，不代表 WHPX 收益。
- 证据：`E:\Boiler-workspace\timing-20261106\run2\` 与 `run2-summary.json`（含 result.json）。

### 尚未完成

- 基线 / 仅资源 / 资源＋去重 / ＋WHPX 的完整分组对比。本轮无法得到：本机 WHPX 不可用；
  基线组需要回退上一轮代码；只读 apk 镜像与 A/B 并行未实施（决策门未达门槛）。
- 真机首次安装验收：不在本机执行，单独记录；上述全部是 TCG 虚拟化结果，不构成硬件验收。

### 首次观测到的下一步优化目标（未实施）

`core:maintenance` 的 `engine.apply` 占每槽约 84% 的 `stage()` 时间（390/423、334/356 秒），
远大于可并行的前置工作。后续提速应优先分析该事务在客户机内的哈希/复制/日志成本；WHPX 若可用，
这部分也会随之加速。该分析不在本轮四项改动范围内。


