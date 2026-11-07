# SteamOS 自有启动分区删除修复（2026-11-07）

## 问题和范围

Windows 安装器原先识别 SteamOS 八角色，却只允许选择后五项；通用 EFI、隐藏 Basic Data 和无访问路径保护会挡住 `esp / efi-A / efi-B`。删除后五项后，完整八角色识别又失效，三个启动分区无法再被识别。

`1106-gm7-after-install.json` 中这三项编号为 7、8、9；编号只是该历史夹具的事实，不是放行规则。未取得用户本次磁盘的新鲜只读快照，不宣称其当前三个分区已经完成归属核验或已经删除。

本次不改适配仓库、TOON、CPU、guest 部署八角色布局、维护器载荷、README 或公开版本。没有提交、推送、打包、发布或对用户物理磁盘执行删除。

## 行为

- 按钮精确改为“选择 SteamOS 分区全部删除”，英文同步。按钮只选择，实际删除仍须管理员 helper 的独立确认及明确勾选同意。
- 完整原生 GPT 八角色可提供八项候选；另外支持仅剩 `esp → efi-A → efi-B` 且连续、类型及 256/64/64 MiB 尺寸匹配的残留候选。其他不完整布局不自动认领。
- 候选不等于删除授权。三个启动角色还须通过 live 原生 GUID、名称、类型、范围、零保护属性、卷 extent、宿主路径和启动内容检查。C# 不覆盖脚本的拒绝结果。
- 不从通用保护类型集合删除 EFI。真实 Windows/宿主、IsBoot/IsSystem、脱机、显式只读、MSR、恢复、保护属性及不能安全检查的卷继续拒绝。
- 对无盘符卷只读发现 native Volume GUID，精确绑定唯一 extent；不分配盘符、不挂载分区、不清除隐藏或 GPT 属性。
- 完整八角色的正向归属来自原生布局；残留组三项还要求 SteamOS 文件内容证据。两者都拒绝未知或共享启动内容。标准 fallback 必须与同卷 `EFI/steamos/steamcl.efi` SHA-256 一致，不能凭 `BOOTX64.EFI` 文件名认领。
- 支持当前 24 文件、`installed` 日志完整且文件哈希一致的项目 `EFI/OXP3-rEFInd` 变体；引用独立 Windows ESP 的菜单不等于同卷有 Windows。旧版、编辑、中断、未知额外文件、同卷 Microsoft 等情况仍保护，不声称支持未经证明的变体。
- 复用原 `SteamOsStarts` 请求和摘要/用途/lease 绑定。所有目标先预检，任一失败则零删除；每次删除前后核对剩余布局，按偏移倒序逐项删除。完整组被逐步拆散后，以首次新鲜快照建立的受限启动角色上下文重做 live 检查，而不是使用陈旧 allow 标志或重新要求八项仍然存在。
- 单项入口使用同样的原生绑定和最终检查。失败立即停止、刷新，不自动重试；已删除分区不可撤销，不承诺恢复数据。

## 已执行验证

大体积及临时媒体放在已核对的健康 RAID 工作区；没有迁移主线或创建并行源码开发目录。

| 交付门 | 结果和边界 |
|---|---|
| 问题复现 | 实际 PowerShell 策略的合成 ESP 用例先因旧 EFI 保护失败，修改后通过；没有修改不可变历史夹具。 |
| 策略/最终目标 | `test-steamos-delete-policy.ps1` 19 项通过：真实策略及最终 rebind 函数，环境/native/content I/O 为 mock，真实磁盘操作禁止。 |
| 编排/原生字节解析 | `test-steamos-partitions.ps1` 40 项通过：八项及三项完整删除集、六项保留、丢失中间组识别后上下文仍有效、倒序、单项、零写预检失败、中途失败/布局变化即停；删除函数为 mock。 |
| 启动内容/extent/路径 | `test-steam-boot-inspection.ps1` 117 项通过，Windows PowerShell 5.1，合成 native seams 和工具自有小文件；不算 native 媒体验收。 |
| 脚本/公开源输入 | `test-script-dependencies.ps1` 通过：PS 语法、dot-source/Backend 引用和 whitelist。独立审阅发现新 helper 曾遗漏 whitelist，已补齐并用该检查回归。 |
| Windows 构建 | `scripts/build-app.ps1 -Configuration Release` 使用锁定 SDK 构建通过，0 warning/0 error。构建及测试命令未使用 ExecutionPolicy Bypass；helper binding 测试的子进程也去除该多余参数，不修改宿主策略。 |
| SteamOS 实际 WPF | `--cli steamos-partition-checks` 86 项通过；中文/英文完整八项和残留三项、真实系统 ESP 仍禁选、取消单项、确认/明确同意、切盘/刷新、短窗口动作可达。1060/640 宽截图均产生，已查看有效渲染及按钮文案；残留组三项状态为可删除 3、已选 3，截图可见启用并勾选的 esp。不是用户当前磁盘画面。 |
| 夹具 | `--cli install-session-checks` 通过，含 7–14 八项派生策略结果与六项保留；原夹具旧五项策略仍明确保持旧值，派生结果不是 native 策略验收。 |
| 其他 Windows 回归 | `--cli helper-binding-checks`、`--cli boiler-checks`、`--cli model-catalog-checks` 通过，分别为安全绑定失败入口、合成镜像/WPF、目录选择回归。 |
| 通用分区 UI | 首次 `--cli partition-picker-checks` 停止于 `next-requires-target`，原失败事实与复现保留。后续修正测试的真实安装源前置条件与显式模式后，**完整 126 项通过**；没有放宽生产源/目标检查、删除原断言或绕过摘要绑定。详见下方后续记录。 |
| 自有 VHD native 读取 | `test-steam-boot-vhd.ps1` 11 项通过：新建 768 MiB file-backed VHD、三个 FAT32 卷的 GUID provider 内容读取、无 Path 的 native 发现、宿主句柄排除，以及真实 EFI 类型无盘符读取。仅初始化/格式化本脚本新建的 VHD；结束已 detach，镜像与报告保留。首次测试因 FAT 卷标超长未匹配而停止，改成合法短卷标后在新 VHD 复跑通过。 |
| VM/VHD 实际删除 | **阻塞/未执行**。生产总线策略不接受 File Backed Virtual，未为让测试通过放宽它；VHD native 读取不等于生产八项/三项实际删除。仍需受控、受支持虚拟总线上的 Windows VM 端到端删除验收。 |
| Python | 项目 portable Python 执行 `-B -m unittest discover -s tests -p 'test_*.py'`，993 项、跳过 98 项，644.595 秒，退出 0，`OK (skipped=98)`。Windows 跳过项不算 Linux 验收。此前系统 `python3` WindowsApps 入口退出 49、无输出，保留该失败日志。 |
| Linux/维护/恢复 | 未运行 Linux 验证。维护器、guest runtime、安装选项与机型载荷未改，本次载荷构建/升级门不适用；删除操作恢复边界为预检、失败停止和刷新，不是分区恢复。 |
| 实机/交付 | **未执行**用户磁盘删除、实机验收、完整或 slim 打包、下载签名/发布检查；不能宣称正式交付或物理设备已验证。 |

## 本地证据位置

- RAID：`E:/Boiler-workspace/steamos-delete-20261107/`，含 policy、sequential、boot-inspection、dependencies、native-vhd 与 Python 日志。VHD 仅为工具自建试验介质，失败/成功证据保留。
- SteamOS WPF：`logs/steamos-reinstall-20261107T122636105/`。
- 首轮本地构建与外部媒体历史账本：`logs/steamos-delete-20261107/evidence.json`，记录当时未提交候选的程序集/启动器大小与 SHA-256，以及自有 VHD 的归属、大小、SHA-256 和结果；不是发布清单，后续构建不覆写该历史结果。
- 机型目录：`logs/model-catalog-20261107T121846904/result.json`。
- generic helper：工作区 `helper-binding-65f213eb504b449f9d1846cbe41d342c/checks.json`。
- `logs/native-cli-result.txt` 是可覆盖的当前指针，不作为多次运行的唯一历史证据。

## 通用分区导航后续修复（2026-11-07）

用户要求继续修复上表中的导航失败。修改前再次实际运行，退出 1，仍为 `next-requires-target`；复现保存在 `logs/partition-navigation-20261107/before.txt`。

根因是测试没有建立它要验证的导航前提：直接从默认模式跳到目标页，没有存在且已检查的源；生产 `Next` 正确地优先返回系统来源页，而旧断言期待停留目标页。旧套件后面还期待“目标 → 启动设置”，但默认 custom 路由先进入安装选项。后续只调整 `app/PartitionPickerChecks.cs` 与 `app/App.xaml.cs` 的测试调度：

- 显式选择 imported 模式，与本套件的直接启动设置路由一致；目标检查保持 InternalTarget 和合成磁盘，不使用 ExternalTarget 绕过目标资格。这不是 custom 路由的新增验收。
- 异步等待真实 `InspectSourceAsync(false)`，使用现有 `RecoveryImageChecks.Fixture()` 生成的工具自有 2 MiB GPT 文件，并将绑定与实际 SHA-256 对照。未注入检查缓存、伪造 digest 或设置 skip。
- 在有效内部目标上新增无源、存在但未检查、已检查后夹具时间戳改变三项来源拦截；仍保留原 `next-requires-target`、非法容量拦截/预览清空、合法目标进入启动设置、返回保留选择。返回后还要求目标页确实可见。
- CLI 等待异步套件结束才记录结果；测试失败也关闭自有窗口并恢复语言。生产 `SourceIssue`、`Next`、物理目标与 helper 保护均未改。

| 后续验证 | 实际结果 |
|---|---|
| Windows Release | 锁定 SDK 构建通过，0 warning/0 error；未使用 ExecutionPolicy Bypass。 |
| 完整分区套件 | `partition-picker-checks` **126 项通过**，原失败断言与其后的导航、布局、确认框检查全部执行；报告 `logs/partition-picker-20261107-214956/checks.json`。 |
| 来源摘要/变更 | `source-digest-checks` **35 项通过**；报告 `logs/source-digest-20261107T135022967/result.json`。 |
| SteamOS 回归 | `steamos-partition-checks` **86 项通过**、无截图错误；报告 `logs/steamos-reinstall-20261107T135042942/checks.json`。 |
| 通用合成回归 | `boiler-checks` **131 项通过**；报告在 RAID 工作区 `boiler-checks-24ee51369ad640c8aef21f8e5bc71d8d/result.json`。其其他截图不自动算本轮视觉验收。 |
| 实际 WPF 画面 | 已查看新分区套件的 `native-step-0.png`、`delete-entry.png`、`delete-confirmation-zh-CN.png`，均有渲染；示例数据分区可删除、Windows/EFI/MSR/恢复行受保护、确认框默认取消且未同意时删除禁用。 |
| 完整性 | 新程序集、启动器、四份 JSON 报告和自有源文件的大小/SHA-256 单独保存在 `logs/partition-navigation-20261107/evidence.json`；不覆写首轮账本。 |

本轮仅为 C# 测试前置条件、CLI 测试入口和记录的后续修改，没有重跑 Python 全套；上表首次 993 项结果仍是独立历史证据。维护/guest/机型载荷和选项未改，相应载荷升级门不适用；Linux、受控 VM 生产删除、用户盘删除、硬件、打包、签名及发布验收仍未执行。没有提交或推送。

安装器本机准备/工作目录必须放 RAID；C: 空间不满足完整准备流程要求。上述路径仅是本地验证记录，不是便携包默认配置。
