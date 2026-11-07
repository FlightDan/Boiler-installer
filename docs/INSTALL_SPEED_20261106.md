# 首次安装提速与重复校验收敛（2026-11-06）

## 背景

三份 SteamOS 安装日志（`E:\SteamOS-logs-2026110*`）显示首次安装总耗时约 2490 秒，其中：

| 阶段 | 耗时 |
|---|---|
| 02.01 组件虚拟机 | 1243 秒 |
| 01.06 容量调整 | 425 秒 |
| 02.03 A/B 独立复核 | 400 秒 |
| 物理写入加回读 | 约 30 秒 |

瓶颈在宿主机以纯软件模拟（`-accel tcg`）启动的 QEMU 客户机，而不是磁盘。本记录覆盖本轮
已实现的部分，并保留严格的来源、物理目标身份和请求绑定检查。

## 已实现

### 计时（仅日志，不参与判定）

- `guest/common.sh`、`guest/integration.sh` 与 `guest/integration-image.py`、`guest/integration-verify.py`
  输出 `BOILER_TIMING=<阶段>:<单调秒数>`。
- `app/Backend.cs` 输出 `BOILER_HOST_RESOURCES=<task>:<qemu 资源参数>` 与
  `BOILER_HOST_TIMING=<task>:vm-start|vm-exit:<秒数>`。

### 客户机资源按宿主机分配

- 新增 `app/GuestResources.cs`：`GuestResourcesPolicy` 是纯函数，`ForHost()` 是唯一的实时宿主读取点。
  - integration、integration-verify、model-maintenance-check：取物理内存 25%，下限 4096 MiB，上限 8192 MiB。
  - 其他任务：2048 MiB；可用内存不足时按 `可用 - 1 GiB` 回退并标记 `host-memory-fallback`。
  - vCPU：`min(8, max(2, 逻辑核数 / 2))`；加速器统一为 `tcg,thread=multi`。
- 删除 `Backend.cs` 中把 `-m` 覆盖成 2048 的旧行；`VirtualBoot.cs` 改用同一函数。
- 单元检查见 `app/GuestResourcesChecks.cs`（经 `--cli boiler-checks` 执行）。

### 去掉重复读取与重复校验

- `app/BoilerImage.cs`：导入镜像时在写入的同一次读取中用 `IncrementalHash` 计算 SHA-256，
  不再写完后重读整个 `imported.img`。带 `ImageManifest` 的 Boiler ZIP 仍与清单比对；
  裸 `.img`、普通 `.zip`、`.img.bz2` 没有预期值，因此不再记录哈希（`ImportResult.Sha256` 为空串，
  日志显示 `not-recorded`）。
- `app/Backend.cs`：`CopyIntegrationPayload` 在同一个源文件句柄上边校验 SHA-256 边写入私有
  临时文件名，校验通过后再重命名到目标位置，替代“先全量哈希、再 `File.Copy` 按路径重读”。
  失败时不留下任何已发布或被截断的文件，清单文件也只在全部校验通过后写入。
- `app/Backend.cs`：设备包导入后不再对目标目录做第二次整包校验；改为断言
  `import-device` 返回的锚定清单摘要等于已审查摘要（`import_local` 已对发布前的完整副本
  做过锚定校验，重命名不改变字节）。
- `protocol/manifest.py`：`validate_package` 增加进程内复用。只有当目录树（相对路径、大小、
  mtime）与上次通过校验时完全一致，才复用结果；出现链接、树变化或未锚定摘要一律走完整校验。
  退出前再次比对树指纹，保证签名与实际校验的是同一份字节。安装与维护钩子在一次运行中对同一
  只读设备包会调用十余次，这一项消除了重复的整包哈希。
- 保留未动：`SnapshotAsync`/`SnapshotBoundSourceAsync` 边复制边哈希并与审查值比对、
  宿主载荷在 guest 内的复核、物理分区写入后的回读比对、`HelperRequestBinding` 等绑定校验。

### vanilla 安装失败

- `app/InstallerOptions.cs`：`Catalog` 现在按 `cache/integration/manifest.json` 的 `available`
  覆盖 `core:*` 选项。只有字面量 `true` 视为可用，与 `scripts/download-firmware.py` 一致；
  安装包未包含某 core 组件时，界面会显示为不可用且不默认勾选，不再等到安装时才报
  `Selected core component unavailable`。
- 设备包组件的可见性语义保持不变：机型包中缺省 `available` 键表示可用，与 `available` 的
  设备侧判定一致，未改动。

### 保留的 MD5 计算

`SourceFileDigest.ComputeAsync` 仍默认计算 MD5。它是与 SHA-256 同一次读取中的附加哈希，
不额外读取镜像，且是界面提供给用户手工比对发布值的入口。真正“无预期值”的整盘重读
（导入镜像、宿主载荷复制、设备包重复校验）已在本轮去除。

## 尚未完成

- **WHPX 硬件加速**：需要重新构建并锁定支持 WHPX 的 `runtime/qemu-min`
  （`bundle-report.json`、`scripts/runtime-dependencies.lock.json`、`docs/DEPENDENCY_BOOTSTRAP.md`）。
  未完成前一律使用 `tcg,thread=multi`。
- **离线 apk 只读镜像**：把 47 个 apk 预构建为 squashfs/ext4 只读镜像以替代每次 `apk add`。
- **A/B 两槽并行**：`stage()` 依赖全局的 `/run/boiler-install-slot` 与共享 home 挂载，
  B 槽机型 EFI 流程还依赖 A 槽，需重构后再并行。
- **guest 内多线程哈希**：待计时数据确认占比后再做。

## 验收边界

- 源测试：`runtime/python/python.exe -B -m unittest discover -s tests -p 'test_*.py'` → 945 项通过，96 项跳过（Windows 跳过的 `fcntl`/FIFO/Unix 权限/符号链接/特权钩子仍需真实 Linux 验证）。
- 构建：`scripts/build-app.ps1 -Configuration Release` 成功，0 警告 0 错误。
- `--cli boiler-checks`：`passed: true`，含新增的 `guest-resources-*`、
  `component-payload-verified-and-published-in-one-read`、
  `component-payload-mismatch-publishes-no-partial-or-verified-bytes` 等检查。
  无头环境下截图渲染为空，`visual_acceptance` 记为 `blocked-empty-render`，**不构成视觉验收**。
- 客户机烟测：`--cli home-copy-plan-checks` 启动真实 QEMU 客户机并通过，
  日志含 `BOILER_HOST_RESOURCES=home-copy-plan-checks:-m 2048 -smp 6 -accel tcg,thread=multi`
  与 `BOILER_TIMING=common:apk:4.060`、`common:modules:6.360`、`vm-exit:138.424`。
- **以上都不构成硬件验收。** 首次安装真机验收、与基线的三组计时对比（基线 / 仅资源 /
  资源＋去重）仍需在 E: RAID 工作区用同一份 X2 Mini 配置单独执行。
