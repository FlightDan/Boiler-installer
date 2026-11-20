# 第三机型开发样例

这是一个可复制的最小模板，用来验证新机型 TOON、生成目录和 `toon-package-v1` 本地首包的工作路径。它只打包一份静态文本配置及其许可证；示例硬件标识是合成值，不能匹配或代表真实设备。样例没有通用资源申请、机型修复、真实设备身份、兼容实测记录或实机验收。它保留 `experimental` 状态供本地候选生成流程验证，但不是已登记的设备适配。

## 复制到适配仓库

在权威 `FlightDan/Boiler-adaptations` 检出根目录创建以下文件，并复制本目录中对应的内容：

| 样例文件 | 适配仓库路径 |
| --- | --- |
| `handheld.toon` | `devices/contributor/handheld.toon` |
| `model.md` | `brands/contributor/handheld/model.md` |
| `editorconfig.sample` | `brands/contributor/handheld/config/editorconfig.sample` |
| `UPSTREAM-LICENSE.txt` | `brands/contributor/handheld/UPSTREAM-LICENSE.txt` |

这些路径中的 `contributor` 和 `handheld` 是稳定示例 ID 的一部分。`editorconfig.sample` 是 Microsoft VS Code 在 [`3b381b139291d9bbdd5a5a1ae60d41dad26d137d`](https://github.com/microsoft/vscode/blob/3b381b139291d9bbdd5a5a1ae60d41dad26d137d/.editorconfig) 提交中的静态 `.editorconfig` 文件副本，`UPSTREAM-LICENSE.txt` 保留该仓库同一提交的 MIT 许可证。包将它们放在文档目录，仅作为静态文件映射示例，不会自动作为编辑器设置启用。真实贡献应按实际品牌和机型命名，并同步更新 TOON 内的身份、源路径、包内路径及目标文件名。不要把本示例文件或身份当作已有公开机型。

## 生成、检查和构建

从 Boiler Installer 检出根目录运行以下命令。将变量换成自己的检出；`$Output` 必须是尚不存在的目录。示例 `source_revision` 固定到静态文件实际来源 Microsoft VS Code 的完整提交。若实际适配引用不同上游，改为该项目中真实使用的完整提交，并保留其许可证和来源记录。

```powershell
$Adaptations = 'PATH_TO_FLIGHTDAN_BOILER_ADAPTATIONS_CHECKOUT'
$Installer = 'PATH_TO_BOILER_INSTALLER_CHECKOUT'
$Output = 'PATH_TO_NEW_LOCAL_PACKAGE_OUTPUT'
$Python = 'python'

& $Python -B "$Adaptations/scripts/refresh-model-source-inventory.py" --root $Adaptations --write `
  --add devices/contributor/handheld.toon `
  --add brands/contributor/handheld/model.md `
  --add brands/contributor/handheld/config/editorconfig.sample `
  --add brands/contributor/handheld/UPSTREAM-LICENSE.txt
& $Python -B "$Installer/scripts/toon_catalog.py" generate --catalog-root $Adaptations
& $Python -B "$Adaptations/scripts/refresh-model-source-inventory.py" --root $Adaptations --write `
  --add brands/contributor/handheld/manifest.json `
  --add models.toon `
  --add adaptations.json
& $Python -B "$Adaptations/scripts/refresh-model-source-inventory.py" --root $Adaptations --check
& $Python -B "$Adaptations/scripts/adaptations.py" check
& $Python -B "$Installer/scripts/toon_catalog.py" check --catalog-root $Adaptations
& $Python -B "$Installer/scripts/toon_catalog.py" preflight --catalog-root $Adaptations --model device:contributor-handheld
& $Python -B "$Installer/scripts/toon_catalog.py" canonical-build --catalog-root $Adaptations `
  --model device:contributor-handheld --output $Output
```

如果没有 Python 3.12 或更高版本，请按安装器的[依赖准备说明](../../DEPENDENCY_BOOTSTRAP.md)操作。生成视图前，先将复制的四个源文件登记到清单；随后再登记生成的 `manifest.json`、`models.toon` 和 `adaptations.json`，第二次清单更新会记录这些文件的最终字节。`adaptations.py check` 会检查生成视图、源文件列表和指南标记。`toon_catalog.py check` 会校验权威适配源快照；构建器根据清单中的文件大小和摘要组装包，并重新导入生成的 ZIP 进行检查。

本示例记录两个不同的提交：`source_revision` 固定上游 Microsoft VS Code 文件的来源；`--source-commit` 则将候选构建绑定到包含 TOON、生成视图、清单和所选文件的确切 `FlightDan/Boiler-adaptations` 提交。本地教程构建省略 `--source-commit`，信任状态保持为 `unsigned-development`；完整源文件集提交后，候选构建应传入该参数。这两项来源信息都不代表已通过硬件验收。

提交真实机型适配前，请将合成 DMI 字段替换为从受测设备读取的值，记录操作系统和内核，并提供所需硬件证据。本示例的静态配置只能说明通用构建器可以携带明确选定的文件；它不能证明安装、维护、恢复或真实设备行为。
