# 为 Boiler 贡献掌机或部件适配

在 [Boiler-adaptations](https://github.com/FlightDan/Boiler-adaptations) 提交 PR。先确定你提交的是整台掌机还是单个部件，两者可以分别贡献。你可以使用 Agent 开发、检查或整理证据。

## 最低实机要求

| 贡献范围 | 提交内容 | 最低功能证据 |
| --- | --- | --- |
| 整台掌机 | 机型 TOON，引用所需公共构件，附上本机型新增的代码和配置 | 这台掌机能启动进入系统，屏幕正常显示，手柄正常输入，并且至少一款游戏实际可正常游玩。写明游戏名称。一次游戏演示可以同时说明多项。 |
| 单个通用部件 | 该部件的适配代码或脚本，以及实际需要的参数或资源 | 在所列硬件上证明该部件工作正常。例如陀螺仪随转动产生输入，或扬声器左右声道能正常出声。 |
| 单个专用部件 | 该部件的前端和后端及对应机型配置 | 证明该部件的实际功能正常，例如操作前端开关并展示硬件响应。仅提交部件时，整机四项和游戏演示均可省略。 |

写明实测硬件、系统和内核版本、复现步骤和观察结果。截图、短视频、日志或可复核的实测记录均可，选能说明功能的证据。机型识别值取自实测机器；Linux 上可读取 `/sys/class/dmi/id/sys_vendor` 和 `/sys/class/dmi/id/product_name`，用 `uname -r` 记录内核。公开前移除序列号、账户及令牌。

## 只贡献一个部件

例如给新款扬声器做适配，可以在 `common/audio/<厂商>/<部件>/` 放脚本、许可证和 `source.toon`。参考[现有扬声器来源记录](../adaptations/common/audio/realtek/alc245/speaker-route/source.toon)，填写自己的资源 ID、硬件匹配条件、兼容版本、文件来源和用途。若功能只适用于一款掌机，将前后端与机型配置放进该机型目录。

通用来源工具会查找 `common/` 下的 `source.toon`。在适配仓库运行 `python -B scripts/common-device-sources.py write`，再用 `scripts/refresh-model-source-inventory.py --write --add <路径>` 登记新增文件，用 `--check` 检查库存。已有机型要随包交付它时，再从该机型 TOON 引用。部件 PR 可独立提交。比如扬声器修复，只需给出能复现播放、能听到预期声道的实测记录。

## 从源码开始

1. Fork 权威适配仓库。机型声明放在 `devices/<品牌>/<机型>.toon`。`brands/<品牌>/<机型>/` 放机型专用前后端、配置和 `model.md`。品牌共用材料放 `brands/<品牌>/common/`，通用部件实现放 `common/`。只贡献部件可以直接提交对应目录和实测证据，无需新增机型。
2. 从机型 TOON 引用已有资源。只申请所需部件，保留 SSD 等构件的硬件匹配条件。新增文件记录来源及适用许可证，引用上游源码时给出精确提交。原作者的源码提交和维护者后来用于发包的适配仓库提交分别记录。
3. 在适配检出根目录，用 `python -B scripts/refresh-model-source-inventory.py --write --add <新文件的相对路径>` 登记新增输入。多个文件可以重复传入 `--add`。在安装器检出根目录，将 `$Adaptations` 设为适配检出的绝对路径，运行 `python -B scripts/toon_catalog.py generate --catalog-root "$Adaptations"` 生成机型目录和指南。回到适配仓库，对新增公共来源或机型公共引用运行 `python -B scripts/common-device-sources.py write`，把新生成的视图也登记入库存，运行 `python -B scripts/refresh-model-source-inventory.py --check` 和 `python -B scripts/adaptations.py check`。
4. 单脚本原型可按[单脚本契约](DEVICE_PACKAGES.md#单脚本契约)打包。新机型首包使用 `toon-package-v1` 构建器，在机型 TOON 的 `build.sources` 列出包内路径与适配仓库源路径的映射，使用稳定的机型及构件 ID。构建器从已锁定的文件生成大小和摘要。`package` 描述组件、目标文件和所需 hook，`package.source` 和 `package.files` 由构建器生成。新机型模板中，顶层 `components` 是指南展示项，沿用 `default: false`。实际安装选项写在 `package.components`，需要默认留空的构件在这里声明 `default: false`。

原作者信息可放在机型或公共部件的来源记录中，例如 `brands/<品牌>/<机型>/provenance.json`；逐项写明上游仓库、完整提交、对应文件与许可证，并在机型组件的 `sources` 和 `build.sources` 中引用该记录。公共资源已有的 `source.toon` 也可逐文件记录 `origin` 和 `license`。这些记录须进入源码库存，维护者仍须审阅其内容；机型指南会保留组件来源路径。机型 TOON 的 `source_revision` 填实际来源仓库和完整提交，可以是贡献者的仓库。普通 fork 生成的包用于本地测试。准备正式候选时，由维护者在权威适配仓库检出经审核的提交；`--source-commit` 将包绑定到该提交及实际交付字节。

复用公共源码时，在 `common_references` 引用来源 TOON，构建器随包保留来源记录与许可证。实际使用的脚本或资源再列入 `build.sources`。如果该资源作为可安装构件交付，可用 `build.resource_bindings` 的 `id`、`definition`、`key`、`component` 指向锁定的完整构件定义。该定义的文件、目标、钩子、依赖及许可证需要与交付内容相符。私有构件无需加入公共资源池，可省略 `resource_bindings` 并使用空的 `resource_model.resources`。驱动、固件及板级参数按实际设备和版本报告测试结果。

在安装器检出根目录构建首包，输出目录应尚不存在。将示例机型 ID 和路径换成自己的。

~~~powershell
python -B scripts/toon_catalog.py check --catalog-root "$Adaptations"
python -B scripts/toon_catalog.py canonical-build --catalog-root "$Adaptations" --model device:example-handheld --output "$Output"
~~~

在普通安装界面选择“导入本地机型包”或拖入生成的机型包，核对身份、构件和来源后确认本次信任。本地包可用于自己的测试。准备正式候选时，维护者从权威适配仓库精确提交重新构建，追加 `--source-commit <完整提交>`；再按[设备索引与签名](https://github.com/FlightDan/Boiler-adaptations/blob/main/docs/REGISTRY.md)生成并核验候选索引。

## 在掌机上检查安装和维护

在普通安装界面导入构建输出中的 `package.zip`，选择要测试的构件，并安装维护器。进入 SteamOS 后，从维护器的机型入口选中刚安装的机型和构件，核对版本并使用“验证文件”。实际操作硬件，按上表记录观察结果。单个部件可以在已有适配的掌机上完成这一步。

普通文件构件可用“恢复原始文件”。提供了 `restore` 钩子的脚本构件，在恢复入口选择安装当前构件版本的包，指向解包后的目录（首次构建输出为 `package/`），审阅本次操作并确认。保留该目录或其原始 ZIP。已通过认证索引升级的构件，恢复时提供该构件当前版本的原包；同机上其他构件可以继续使用各自的版本。维护器的已安装机型入口也能在离线时找到尚未收录的本地机型。机型进入正式设备索引后，维护器可以按索引里的唯一 DMI 匹配发现更新，即使本机的离线目录较旧。旧安装如果没有可核对的事务收据，维护器无法补认脚本恢复权限。

需要从空白机型开始时，可复制[第三机型开发样例](examples/contributor-handheld/README.md)。样例只含静态文本配置、合成硬件标识和空资源申请；它不是已登记机型，也不提供实机验收证据。

## PR 可直接填写

~~~text
贡献范围（整机 / 通用部件 / 专用部件）
品牌和机型 / 部件硬件标识
系统与内核版本
代码位置、引用的已有构件、第三方来源与许可证
复现步骤和实测结果
整机填写：启动、屏幕、手柄、实际游玩的游戏名称和结果
部件填写：部件功能、操作和可观察到的结果
证据链接或附件
已知限制和未测试项目
~~~

维护者收到 PR 后，使用 Agent 检查改动的代码、脚本及载荷中是否有恶意行为，核对实际执行的文件、请求的权限和来源，并审阅相应范围的最低功能证据。通过后由维护者合并。签署正式设备索引和发布由维护者完成。源码合并、软件检查、实机验收及发布状态分别记录。

### 维护者生成索引候选

在固定提交的安装器检出根目录运行。`$Adaptations` 指向已合并改动的权威适配检出，`$Output` 是用该提交及 `--source-commit` 生成的包目录。`$Previous` 和 `$Signature` 指向上一份公开索引及其签名，`$AssetUrl` 填本次 ZIP 的固定 Release 附件地址，`$Candidate` 是尚不存在的候选索引文件路径。

~~~powershell
python -B -m protocol index "$Previous" "$Signature"
if ($LASTEXITCODE -ne 0) { throw "上一份设备索引验签失败" }
$PreviousHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Previous).Hash.ToLowerInvariant()
$NextSequence = 1 + (Get-Content -Raw -LiteralPath $Previous | ConvertFrom-Json).sequence
$Version = (Get-Content -Raw -LiteralPath "$Output/package/manifest.json" | ConvertFrom-Json).version
$SourceCommit = git -C "$Adaptations" rev-parse HEAD
$InstallerCommit = git rev-parse HEAD

python -B scripts/toon_catalog.py index-candidate --source-checkout "$Adaptations" `
  --model "$Output/package" "$Output/package.zip" "$AssetUrl" --version "$Version" `
  --previous-index "$Previous" --previous-sha256 "$PreviousHash" --sequence $NextSequence `
  --source-commit "$SourceCommit" --installer-root "$PWD" --installer-commit "$InstallerCommit" `
  --output "$Candidate"
~~~

这一步生成待签名索引，可以单独追加本次机型。签署和发布由维护者完成。
