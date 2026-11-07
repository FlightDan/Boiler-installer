# 维护事务 I/O 收敛与本机缓存切换（2026-11-07）

本记录接续 [INSTALL_SPEED_FOLLOWUP_20261106](INSTALL_SPEED_FOLLOWUP_20261106.md) 的
“下一步优化目标”。该记录把 `core-maintenance` 390/334 秒当作主要耗时；本轮核实的结论是：
那份计时来自 `C:\Boiler-installer\cache\` 残留的旧方法缓存，不是新方法的实际开销。

## 结论与根因

- 旧缓存 `cache/integration/manifest.json` 为 `local_test_models: True`，
  `core:maintenance` 含 569 个文件 / 492,693,398 字节，OXP3 与 X2 Mini 的旧展开目录
  全部塞进了通用维护组件；`cache/model-bundles` 为 `LocalTestModelBundles`，
  544 个文件 / 492,398,659 字节。
- 新方法（`LocalTestModelBrandBundle`）在 `.11` 交付中已生效：品牌 ZIP 单份去重，
  所选机型经 `models._retain` 在目标盘留存；`core:maintenance` 只有 88 个通用维护文件。
- 本机 `cache/` 不随 git 提交，10 月 30 日生成的旧缓存一直留在工作树里；阶段 5 的
  `--cli boiler-model-checks ... cache/model-bundles/packages/x2-mini` 实际把旧 OXP3 载荷
  送进了客户机，所以测出 390/334 秒。

## 已实现

### 本机缓存切换到新方法

- `cache/model-bundles`：改为 `LocalTestModelBrandBundle`。
  - `onexplayer.zip` 446,833,213 字节，SHA-256
    `8b9a72749507855f46d5c2d1624f53315d6f21c5562b7f42226d3642bdb4a89a`；
  - OXP3 391 个文件、X2 Mini 390 个文件，共享运行成员在 ZIP 中只存一份；
  - 两机型都通过了 `bundled_model_package(..., trust_local_test=True)` 的完整导入校验。
- `cache/integration`：用 `scripts/prepare-integration.py --decky cache/prepared-decky-v3.3.1`
  按当前源码重新生成。
  - `core:maintenance` 88 个文件 / 1,008,061 字节，无 `local_test_models`（在引擎最终改动后
    重新生成，并核对 `maintenance-runtime/` 中的 `engine.py`、`adoption_blobs.py` 与源码一致）；
  - `core:decky` 1 个文件 + 2 条 notices（Decky 3.3.1，来自既有锁定输入）；
  - 用 `scripts/package-installer.py` 的 `validate_integration` 从最终源码重建并比对通过。
- 旧缓存清单与大小已记录在
  `E:\Boiler-workspace\cache-switch-20261107\inventory-before.json`
  （integration 573 文件 / 529,381,173 字节，清单 SHA-256 `d665507c…`；
  model-bundles 544 文件 / 492,398,659 字节，清单 SHA-256 `552ff4f1…`）。
  替换验证通过后删除，均为可再生成的中间产物。

### 安装器检查适配品牌包

- 新增 `app/BundledModelManifest.cs`：按描述符的 `manifest_sha256`/`manifest_size`
  从品牌 ZIP 中读取所选机型的 manifest（或旧目录布局），不整包解压；先校验 ZIP 文件大小。
- `BoilerImageBuildChecks`、`BoilerCustomBuildChecks`、`BoilerModelCatalogChecks`
  不再写死 `cache/model-bundles/packages/<model>/manifest.json`；新文件加入
  `scripts/source-files.json` 白名单。
- `BoilerCustomBuildChecks` 的 BE201 文案断言改为按所选品牌包是否包含
  `resource:intel-be201-firmware` 分支，与 `CurrentHardwareAvailability` 的既有语义一致；
  旧断言只对没有 BE201 资源的旧缓存成立。

### `fsync` 收敛（方法 2）

- `maintenance/adoption_blobs.py` 的 `restore_blob`：同一描述符只保留一次
  `os.fsync`（覆盖数据与 `fchown`/`fchmod` 元数据），不再在元数据前后各刷一次。
- apply 批次内每个受影响的父目录只在它的最后一次 `os.replace` 后同步一次；
  `remove_blob_target` 同样接受 `sync_directory`。恢复、恢复屏障和外部调用仍默认逐文件同步。
- 私有 CAS 目录在该事务的准备循环结束后同步一次（无论最后一次调用是否新建对象），
  且在 prepared 日志写出之前完成；CAS 文件本身的 `fsync` 顺序保持不变，恢复时仍逐对象校验。

### `Engine.apply` 重复读取收敛（方法 3，收窄）

- 保留准备阶段对载荷源文件的 `_hash_file` 校验。CAS 对象已存在时（同一次安装的 B 槽、
  重复安装）这是唯一的载荷校验，不能删。
- 保留写前检查：批次前一次全量检查，并在每个目标自身替换前复查。
- 保留写后检查：每个操作（包括无 hooks 的 blob 组件）在状态提交前都重新观察。
- 保留并启用：同进程内对私有 CAS 对象的摘要复用（`_verified_private`，仅 POSIX）。
  命中仍经过 no-follow 遍历与私有所有权检查，签名含 ctime。它消除的是 B 槽对已存在
  CAS 对象的整文件重读。
- 撤回两项（审查后确认）：先前我以“写前检查之间没有外部进程”为由删除逐目标复查，
  并跳过 blob 组件的写后观察。该理由不完整：桌面会话会在事务运行期间改写 home 下的配置
  （代码中 kwinrc 已有专门处理）。删除后，外部写入的窗口从单个目标扩大到整个批次，
  且写后观察缺失时，外部写入可能在状态提交后才暴露。两项的代价只是重新读取当前目标
  与刚写入的文件（核心组件约 1 MB），撤回的成本很小。
- 收益边界：新布局下核心组件只有 88 个文件、约 1 MB；`oxp3:legacy-adaptation` 的清单
  没有逐文件条目，数据由其钩子处理，不经过 Engine 的逐文件 CAS 路径。因此方法 3
  在当前布局下收益有限。前文“预计降至 60–90 秒”的估计对应旧布局（492 MB 经 CAS），
  不适用于当前布局，不应作为依据。

## 回归

- `tests/test_maintenance.py` 新增两项 Linux 专属回归：
  - 一个 apply 中同一目录的 3 个文件只触发 1 次目录 `fsync`，而恢复仍逐文件同步；
  - 私有 blob 第二次校验复用摘要（不再读取），`chmod`/`utime` 后必须重新校验。
- CPU 进程杀测试与其它 `_put_snapshot` 桩接受 `sync_directory` 关键字；
  行为断言（恢复屏障、写后校验、目标未被修改）保持不变。

## 交付门

| 门 | 状态 |
| --- | --- |
| 安装 | 完成：宿主包准备与客户机集成未改判定；`engine.apply` 事务、日志与恢复格式不变。 |
| 维护 | 完成：`_put_snapshot`/`restore_blob` 仍默认逐文件同步；维护升级与恢复路径未改。 |
| 选项/元数据 | 完成：品牌包的机型 manifest 由新检查读取；`core:decky` 可用性恢复到旧缓存行为。 |
| 运行时载荷 | 完成：`cache/integration` 由最终源码重建并通过 `validate_integration`；品牌包哈希与描述符一致。 |
| 恢复 | 完成：恢复路径逐文件目录同步与写前/写后校验保留；CPU 中断恢复测试通过。 |

## 验收边界

- Python 全量：958 项通过、98 项跳过（Windows 跳过的 `fcntl`/FIFO/Unix 权限/符号链接/
  特权钩子，以及本轮新增的两项 POSIX 专属回归，仍需真实 Linux 验证）。
- Windows Release 构建：0 警告、0 错误。
- `--cli boiler-checks`：`passed: true`，`visual_acceptance=captures-produced-needs-review`；
  截图仍需人工查看，不构成视觉验收。
- `--cli model-catalog-checks-no-capture`、`--cli model-plugin-checks`、
  `--cli custom-build-checks-no-capture`：通过。
- 独立审查：完成。修复 1 项新测试缺陷（Linux 私有所有权检查会在断言前拒绝 0o644
  源文件），并在 Windows 上关闭进程内摘要复用（其 `st_ctime_ns` 可被还原）；
  CAS 目录首次创建时父目录的 mkdir 持久化为既有行为，列为残余风险。
- 证据目录：`E:\Boiler-workspace\cache-switch-20261107\`（切换前清单、各检查结果、
  两份新 manifest、全量测试日志）。

## 尚未完成

- 客户机计时与集成验证：已在一次性 QEMU 夹具上启动 `--cli boiler-model-checks`（X2 Mini
  品牌包，完整选择，A/B 两槽），后台任务 `by0yt96pm`。结果、`stage-A/B:core-maintenance`
  与整体 `integration` 时长需在结束后另行记录；该计时不构成硬件验收。
- 此前用于启动的后台 PowerShell 调用会在后台任务结束时带走子进程，未得到结果；
  改用 Bash 后台调用后正常运行。
- `guest/model-maintenance-check.py`（`--cli boiler-linux-model-checks`）仍按旧的本机缓存
  镜像布局读取 `usr/local/lib/boiler/cache/model-bundles/packages/oxp3`，并把 X2 的
  “无可用包”状态直接当作布尔值。品牌包/留存包方法下该缓存不存在，需要改为检查
  `var/lib/boiler/maintenance/models/packages/<sha256>` 与留存指针（并单独处理无包错误）
  之后才能作为验收门。本轮未运行、未修改该入口。
- 新增的两项 POSIX 回归和 `fcntl` 等 Windows 跳过项需要受控 Linux root 验证；
  Windows 上这些路径不执行。
- CAS 目录首次创建时其父目录 `.boiler` 的 mkdir 持久化沿用既有行为（`_directory` 只在
  显式设置 `create_uid` 时同步父目录），本轮未改变；“CAS 在 prepared 日志前已同步”
  的保证只覆盖目录内的新对象，不覆盖首次创建整棵目录树的断电场景。
- 实体机首次安装、维护升级与恢复验收仍未执行。

