# SteamOS 启动分区识别放宽（2026-11-13）

## 问题和范围

用户在「安装位置」页只能选择并删除普通数据分区，`esp / efi-A / efi-B` 三个启动分区始终灰掉。界面显示的 `oxp3_stage / steamos-efi-a / steamos-efi-b` 是卷标，不是判定依据。

已确认的事实（用户提供，未取得本轮新鲜只读快照）：

- 目标盘为 GPT，三个分区的 **GPT 名称**等于 `esp / efi-A / efi-B`。
- 盘上只有这三个分区带角色名，不存在重复角色。
- 三者容量正好是 256 / 64 / 64 MiB。

名称、数量、尺寸三项均满足而识别仍失败，说明失败点在其中一条更窄的门禁上。用户决定：识别条件只保留**名称、数量、连续性**，其余门禁删除。

本次不改适配仓库、TOON、CPU、guest 部署布局、维护器载荷、README 或公开版本。没有提交、推送、打包、发布，也没有对任何物理磁盘执行删除。

## 行为变化

### 识别契约

识别现在只要求：GPT 名称精确匹配角色名、数量满足要求、按偏移首尾相接。以下不再是识别门禁：

- 分区类型 GUID 与策略表的比对
- 策略表声明尺寸（`bootSize`）与实际分区尺寸的比对
- `nativeGptVerified`
- `gptAttributes == "0"`

`steam-partition-roles.json` 的 `bootSize` 字段保留：安装规划 `DiskPlanning.Layout` 与 guest 侧布局仍在使用，删除会与它们脱节。它现在只描述准备布局，不再作为识别条件。

`bootSize` 相关的过滤改为按名称判断启动角色（`esp` / `efi-` 前缀），不再用 `bootSize > 0` 反推角色。

### 删除策略

- 角色归属仍按名称解析，但不再比对类型、尺寸、GPT 属性。
- **保留** GUID / offset / size 的原生身份绑定：它不是识别门禁，作用是保证审核快照与实际分区仍是同一个分区。
- **保留** 只读保护。

### 引导内容检查

正向 SteamOS 归属证据不再是删除前提，删除：`steamos/conf` 配置证据、`steamcl.efi` 存在性与 SHA-256 等值、槽位 `grub.cfg` 的 `steamenv_boot` / `/boot/vmlinuz-` 证据、项目 rEFInd 的 24 文件清单与字节日志比对。

**保留** 阻止不可逆破坏的检查：

- `EFI/Microsoft`、`EFI/ubuntu` 等他系统厂商目录
- `bootmgfw` / `bootmgr` / `memtest` / `BCD`
- 共享引导配置文本（`chainloader`、`microsoft`、`windows` 等）
- 重解析点

`EFI/BOOT/BOOTX64.EFI`（通用可移动回退加载器）、`EFI/OXP3-rEFInd`（本项目自有）现按正常内容处理，不作为他系统证据。

### 未改变的保护

磁盘离线/只读、非 GPT、总线白名单、512 字节扇区、`IsBoot` / `IsSystem` / `IsOffline`、MSR / Windows 恢复 / BIOS boot 类型、宿主保护路径（安装器目录、`%SystemRoot%`、TEMP、pagefile、崩溃转储）、删除时逐项重新绑定与失败即停。

### 移除的死代码

`Get-SteamOsBootRefindBlockReason` 不再有调用者，改为一行 fail-closed 拒绝，保留函数名以免外部调用静默放行。

## 已执行的验证

| 交付门 | 结果和边界 |
|---|---|
| 分区识别/编排 | `test-steamos-partitions.ps1` 通过。新增用例：类型、原生属性、末位尺寸不再阻止识别；中位尺寸变化仍因破坏连续性被拒。 |
| 删除策略 | `test-steamos-delete-policy.ps1` 21 项通过。新增：GPT 属性与原生名称不再是门禁；声明尺寸不匹配仍拒；非启动角色不能走启动分支。 |
| 引导内容 | `test-steam-boot-inspection.ps1` 通过。改为：空卷、通用回退、未绑定回退、rEFInd 变体、损坏日志不再阻止；同卷 Microsoft、外来厂商目录、共享配置、重解析点仍阻止。 |
| 脚本/白名单 | `test-script-dependencies.ps1` 通过（含本记录的 whitelist 登记）。 |
| Windows 构建 | `build-app.ps1 -Configuration Release` 通过，0 warning / 0 error。 |
| SteamOS WPF | `--cli steamos-partition-checks` 通过，含中文/英文、1060/640 宽截图，截图为渲染产物但未逐张人工复核。 |
| 历史夹具重放 | `--cli boiler-checks` 通过。`1106-gm7-after-install.json` 的旧五项策略值未被改写；`gptType` 突变用例改为「仍识别」，名称突变仍需拒绝。 |
| 通用分区 UI | `--cli partition-picker-checks` 通过。 |
| 机型目录 | `--cli model-catalog-checks` 通过。 |
| Python | 1050 项，99 跳过，15 错误。**15 项全部因本机 `git` 不在 PATH（`FileNotFoundError: [WinError 2]`）而失败**，属环境缺失，与本改动无关；未声称 Python 全通过。 |
| 目标盘实际识别 | **未验证**。本机 `read-disks.ps1` 实读无该目标盘（`logs/plan-read-disks-20261113.json` 仅 disk 2/3/4/5）。 |
| 实机删除/打包/签名/发布 | **未执行**。这些是独立验收门。 |

## 边界和风险

- 识别放宽后，一个名称恰好匹配 `esp / efi-A / efi-B` 且三者连续的分区，即使类型或尺寸不符也会被认作 SteamOS 启动分区。真正的删除障碍只剩「含他系统引导内容」和宿主路径/系统分区保护。
- 若目标机上三者**不首尾相接**，放宽后仍不会识别。届时需要一份新鲜的只读快照定位，不应继续放宽。
- 本次没有取得目标盘的新鲜快照，不能宣称该盘三个分区已完成归属核验或已删除。

## 本地证据位置

- 策略/编排/引导内容/依赖检查：`work/relax-20261113/{partitions,policy,boot,script-dependency-checks-*}`。
- CLI 套件：`work/relax-20261113/{cli4,cli5,partition-picker-checks,model-catalog-checks}`；`logs/native-cli-result.txt` 是可覆盖的当前指针。
- 本机只读磁盘读取（不含目标盘）：`logs/plan-read-disks-20261113.json`。
