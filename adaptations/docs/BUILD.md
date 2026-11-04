# 构建机型包和安装器

[首页](README.zh.md) · [English](BUILD.en.md) · [TOON 指南](TOON.md)

从机型 TOON 找到组件和共用资源，再用 Boiler Installer 的工具准备源码快照与构建输入。本页以适配仓库提交 `3edd6a340b56628785f05365d62349f7d03c63eb` 和安装器提交 `04dce422d0c4831ced6d2b9efdbd852e7b02a724` 为例；使用其他版本时，请同时核对它们各自的输入锁。

## 仓库分工

| 位置 | 内容 |
| --- | --- |
| [Boiler-adaptations](https://github.com/FlightDan/Boiler-adaptations) | 机型 TOON、适配源码、共用资源和设备索引 |
| [Boiler Installer](https://github.com/FlightDan/Boiler-installer) | 源码快照、机型包、维护工具和安装器的构建代码 |

两边各有自己的源码清单。适配仓库更新后，构建项目需要使用指定提交重新准备快照，不能只改一个链接就当成输入已经更新。

## 1. 检查 TOON 和源码快照

以下命令在 **Boiler Installer** 仓库根目录运行。这个安装器提交已包含绑定适配仓库 `3edd6a3` 的源码快照：

```powershell
python -B adaptations/scripts/toon_catalog.py check
```

要从适配仓库重新导出该提交的快照，先准备一个来源为 `FlightDan/Boiler-adaptations` 的独立检出，然后将下面两个路径换成实际路径；输出目录须是新目录：

```powershell
$Adaptations = "PATH_TO_BOILER_ADAPTATIONS_CHECKOUT"
$Snapshot = "PATH_TO_NEW_SNAPSHOT_DIRECTORY"
python -B scripts/prepare-canonical-model-snapshot.py --source-checkout "$Adaptations" --source-commit 3edd6a340b56628785f05365d62349f7d03c63eb --output "$Snapshot"
```

该命令从指定 Git 提交读取 `model-source-files.json` 中列出的文件，验证大小和哈希，再输出快照。它不会自动替换安装器检出中的文件；更新构建项目的快照时，也要同步其来源锁和源码清单。

## 2. 按机型申请准备资源池

资源池工具读取快照中两台机型的 `resource_model.resources`，核对各自依赖，再根据两份清单的并集准备资源。把输出路径换成尚不存在的新目录：

```powershell
$Pool = "PATH_TO_NEW_RESOURCE_POOL"
python -B scripts/prepare-resource-pool.py --project . --check-inputs
python -B scripts/prepare-resource-pool.py --project . --output "$Pool"
```

它生成资源池和目录，不是完整机型包。目前扫描快照中的全部机型，两台当前申请的 14 个资源相同。`common_references` 是另一个字段并单独核对：其中 xe、BE201 和 ALC245 的资源 ID 与这 14 项重叠，BMI260 和 GM7 启动策略另有引用。按实际资源路径核对，不要把重复引用当成额外实体文件。

资源池是独立输出，当前 `canonical-build` 不接收这个目录；下一节使用基础机型包和另行准备的共用资源 stage。

## 3. 准备共用资源并构建一台机型

下面以 X2 Mini 为例。先准备好经过核对的 **X2 完整基础机型包目录**、该包已审核的 manifest SHA-256，以及固定来源的公开安装器 ZIP。构建器还需要当前项目锁定的资源输入；具体来源见 [共用资源准备说明](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/adaptations/docs/CANONICAL_COMMON_PREPARATION_20261103.md)。

在 PowerShell 中替换以下路径和哈希；`$Stage` 和 `$Output` 须指向尚不存在的新目录：

```powershell
$Base = "PATH_TO_REVIEWED_X2_PACKAGE_DIRECTORY"
$BaseSha = "REVIEWED_BASE_MANIFEST_SHA256"
$PublicInstaller = "PATH_TO_PINNED_PUBLIC_INSTALLER.zip"
$Stage = "PATH_TO_NEW_COMMON_STAGE"
$Output = "PATH_TO_NEW_MODEL_OUTPUT"

python -B scripts/prepare-canonical-common.py --canonical-snapshot --gyro-public-archive "$PublicInstaller" --output "$Stage"
python -B adaptations/scripts/toon_catalog.py canonical-build --model guide:x2-mini --input "base=$Base" --input "stage=$Stage" --input "public-installer=$PublicInstaller" --base-manifest-sha256 "$BaseSha" --output "$Output"
```

第一个命令准备共用设备输入；第二个命令读取 X2 的机型资料，核对快照、基础包和输入，再生成一台机型的未签名候选包。OXP3 使用 `guide:oxp3` 和对应的 OXP3 基础包。基础包及其实际来源、准备步骤见 [机型构建说明](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/adaptations/docs/CANONICAL_MODEL_FINALIZATION_20261103.md)。

## 4. 裁剪、复用和打包

两台机型的目标品牌包应按两份 TOON 选择文件：检查 `components` 的 `sources`、`resource_model.resources`、`common_references` 和 `boot_policies`，沿路径收集申请的文件及依赖，保持适配仓库的目录结构；两份清单引用同一路径时只存一份，机型专属文件分别保留。检查清单、许可证、完整性以及安装、维护和恢复所需内容。

当前 `canonical-build` 基于已有完整基础包构建**单台机型**。按两份 TOON 裁剪并去重生成一个品牌包，需要对应的裁剪构建器和包内容核对；不要把单机型完整基础包构建结果当成该品牌包。

可以从这些代码开始：

- [TOON 解析、检查和 canonical-build 入口](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/adaptations/scripts/toon_catalog.py)
- [源码快照准备器](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/scripts/prepare-canonical-model-snapshot.py)与[共用资源准备器](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/scripts/prepare-canonical-common.py)
- [DevicePackage 格式](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/schemas/DevicePackage.schema.json)和[包检查代码](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/protocol/manifest.py)

已有符合格式的机型包目录时，可以生成未签名 ZIP：

```powershell
$Package = "PATH_TO_PACKAGE_DIRECTORY"
$Zip = "PATH_TO_NEW_PACKAGE.zip"
python -B scripts/build-device-package.py "$Package" --output "$Zip"
```

## 5. 生成 Windows 安装器

机型包和安装器是不同产物。构建安装器时，在 Boiler Installer 的 Windows x64 独立检出中准备锁定依赖、构建应用，再准备集成内容并运行打包脚本。入口见 [Boiler Installer 构建说明](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/README.md#build-from-an-independent-checkout)和[依赖准备文档](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/docs/DEPENDENCY_BOOTSTRAP.md)。

已有机型包需要加入设备目录时，在 **Boiler-adaptations** 仓库根目录使用[设备索引流程](REGISTRY.md)；`scripts/build-index.py` 接受已生成的包，不负责构建包本身。

上述命令固定对应本页注明的历史安装器提交。迁移后的安装器工具入口为 `scripts/toon_catalog.py`；新 X2 桥接入口使用显式 `--project-root` 指向该工具，历史桥接文件及来源记录保持不变。实际使用须先取得包含这些更改的精确提交并核验对应快照。
