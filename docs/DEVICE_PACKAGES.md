# 外部机型脚本与机型包

机型页会自动读取 GitHub 机型目录。目录中的固定版本包经索引签名及文件清单校验后才能按目录来源使用；机型适配状态和硬件验收范围仍以条目的记录为准。

自行编写或从其他作者取得的适配可以拖入机型页，也可以通过文件选择入口导入。支持下列输入：

- 符合本文契约的单个 `.sh` 脚本。
- 根目录具有 `manifest.json` 的机型包文件夹。
- ZIP、tar、tar.gz、tar.xz 包；归档可以直接包含包文件，也可以只有一个外层文件夹。额外说明文件应放入包清单，不能与外层文件夹并列。

可以把机型包和恢复镜像、3.10 系统文件一起拖入窗口，也可以分批追加。拖入先只读识别文件内容并显示关联位置，不立即修改安装配置或运行脚本。勾选本次信任，再点击“一键关联当前配置”后，才会完整校验机型包并把它纳入选择。重复位置可选择“暂不关联”或移除；普通组件和安装位置保留当前选择。切换安装方式或重新启动安装器后，外部包需要重新确认信任。后续安装确认及完整来源检查仍按正常流程进行。

未知扩展名、缺少清单的包、普通 Bash 脚本、路径穿越、链接或 junction、重复归档路径、多余文件及大小或 SHA-256 不符都会被拒绝。导入器不尝试运行脚本以猜测其功能。Python 接口 `import_local(source, destination, developer=True)` 中的 `developer=True` 只授权读取和导入；调用者仍须单独建立本次选择及后续特权执行授权。

## 单脚本契约

脚本必须采用 UTF-8、LF 换行，无 BOM 或 NUL，大小不超过 8 MiB。第一行必须是 `#!/bin/bash` 或 `#!/usr/bin/env bash`。前置注释区须声明 `# boiler-script: 1`，可使用 `# boiler-name: 显示名`；声明应位于前 64 行内、第一条实际命令之前，且不能重复。

执行约定为：

```text
bash device.sh PHASE TARGET_ROOT
```

English contract: argument 1 is `apply`, `verify`, or `restore`; argument 2 is the absolute mounted target root. `apply` changes only that target and must support retry. `verify` checks the result without changing the target. `restore` reverses the author's changes conservatively and must support repeated calls. Never assume `/` is the installation target. Importing or packaging does not execute this contract.

下例只在指定目标的 `/etc` 中创建一个演示文件。它拒绝覆盖不同内容或链接；恢复在文件不存在时成功，可重复调用。请只在可丢弃的测试系统中试用，先将文件保存为 `example-device.sh`：

```bash
#!/bin/bash
# boiler-script: 1
# boiler-name: Boiler script example
set -euo pipefail
phase=${1:?Expected apply, verify, or restore}
root=${2:?Expected mounted target root}
[[ "$root" = /* && -d "$root" && ! -L "$root/etc" ]] || exit 2
file="$root/etc/boiler-script-example.conf"
expected='boiler-script-example=1'
matches() { [[ -f "$file" && ! -L "$file" ]] && [[ "$(cat -- "$file")" = "$expected" ]]; }
case "$phase" in
    apply)
        if [[ -e "$file" || -L "$file" ]]; then
            matches || { echo 'Existing file differs; refusing overwrite' >&2; exit 1; }
        else
            mkdir -p -- "$root/etc"
            (set -o noclobber; printf '%s\n' "$expected" > "$file")
        fi
        ;;
    verify)
        matches || { echo 'Example file verification failed' >&2; exit 1; }
        ;;
    restore)
        if [[ -e "$file" || -L "$file" ]]; then
            matches || { echo 'Modified file; refusing removal' >&2; exit 1; }
            rm -- "$file"
        fi
        ;;
    *) echo 'Expected apply, verify, or restore' >&2; exit 2 ;;
esac
```

这是契约演示，并未实现完整备份、独立所有权凭证、并发保护或断电恢复。实际适配作者必须明确各阶段的边界、冲突处理、原始文件恢复、支持的系统及内核；`verify` 的只读性和 `restore` 的正确性不会因声明存在而自动获得保证。普通执行型 `.sh` 不能只加一行声明后直接导入：必须先把安装、只读验证和恢复逻辑分别迁移到三个阶段，并改用第二个参数定位目标根目录。

导入器保留原脚本字节，并生成三个固定 wrapper，将安装引擎提供的根目录转换为上述两个参数。自动生成的包默认是 `experimental`、`unsigned-development`，架构为 `x86_64`；未限制 OS 不代表已经验证全部 OS。自动封装不添加 `maintenance_entry`，不声明已经通过硬件验收。

## 构建 ZIP

以下 PowerShell 命令均从安装器根目录运行，使用随安装器准备的 Python：

```powershell
& .\runtime\python\python.exe -B .\scripts\build-device-package.py .\example-device.sh --output .\example-device.zip
```

输出路径必须尚不存在。工具从实际脚本和固定 wrapper 重新生成文件大小及 SHA-256，验证完整包，写入 ZIP，再重新导入该 ZIP 校验其内容。成功后打印 ZIP 的绝对路径、大小、SHA-256、manifest 摘要及机型包 ID。此工具不读取私钥、不签名、不发布，也不会授予安装授权。

生成的 ZIP 内部结构如下：

```text
manifest.json
scripts/
  device.sh
  apply.sh
  verify.sh
  restore.sh
```

以下是生成清单的结构示意，摘要占位符不是可用清单。实际 ID、文件大小和摘要由工具计算，不要复制占位符，也不要手改哈希以掩盖文件损坏：

```json
{
  "schema_version": 1,
  "kind": "DevicePackage",
  "id": "device:local-<script-digest-prefix>",
  "version": "0.1.0",
  "name": "Boiler script example",
  "status": "experimental",
  "trust": {"status": "unsigned-development", "official_release": false},
  "source": {"kind": "local", "path": "example-device.sh"},
  "compatibility": {"architectures": ["x86_64"]},
  "files": [
    {"path": "scripts/device.sh", "size": "<computed integer>", "sha256": "<computed SHA-256>"},
    {"path": "scripts/apply.sh", "size": "<computed integer>", "sha256": "<computed SHA-256>"},
    {"path": "scripts/verify.sh", "size": "<computed integer>", "sha256": "<computed SHA-256>"},
    {"path": "scripts/restore.sh", "size": "<computed integer>", "sha256": "<computed SHA-256>"}
  ],
  "components": [{
    "schema_version": 1,
    "kind": "ComponentManifest",
    "id": "local-script:<script-digest-prefix>",
    "version": "0.1.0",
    "name": "Boiler script example",
    "default": true,
    "compatibility": {"architectures": ["x86_64"]},
    "files": [],
    "hooks": {"apply": "scripts/apply.sh", "verify": "scripts/verify.sh", "restore": "scripts/restore.sh"}
  }]
}
```

## 已有机型包与维护边界

已有包必须准确列出除 `manifest.json` 外的全部文件，组件文件和 hook 必须引用清单内的文件。每条记录的大小与 SHA-256 都要匹配真实字节。可以直接导入既有包文件夹或归档；也可以使用同一工具将已验证的文件夹或归档重打包：

```powershell
& .\runtime\python\python.exe -B .\scripts\build-device-package.py .\my-device-package --output .\my-device-package.zip
```

对于已有包，工具严格验证并保留原始 manifest，不会重写其中的哈希来“修好”不匹配。包作者修改载荷后应通过自己的可复现构建过程重新生成清单，核对实际文件和独立版本。只检查已有文件夹时可以运行：

```powershell
& .\runtime\python\python.exe -B -c 'import sys; sys.path.insert(0,"."); from protocol.__main__ import main; sys.exit(main())' package .\my-device-package --developer
```

此命令只做本地完整性检查，成功不代表来源已经认证或脚本安全。清单的 `source` 必须符合现有协议：本地来源为 `local`，GitHub 来源应记录完整固定 commit，HTTPS 来源应记录固定资源及真实摘要。文件内自行宣称的信任状态不能替代签名目录锚点。

新装机型包、维护专用包和维护升级是不同入口。`purpose: "maintenance"` 的包应交给通用维护器，不能作为新装机型包。含 hook 的脚本包即使有 `restore` 阶段，也不会自动具备维护升级资格；维护器还要求其支持的显式维护契约、认证计划和既有所有权检查。任意脚本副作用不能由文件事务自动完整回滚。

外部包的本次信任与公开目录审核发布相互独立。让外部包进入签名目录需要固定来源、完整载荷校验、版本及索引序列管理、签名和适用验收；拖入、生成 ZIP 或本地校验都不会完成这些步骤，也不会修改已发布目录。
