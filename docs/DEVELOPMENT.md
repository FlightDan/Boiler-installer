# 开发与构建

[返回项目首页](../README.md) · [文档索引](INDEX.md) · [依赖准备详情](DEPENDENCY_BOOTSTRAP.md)

Boiler Installer 的 Windows 程序位于 `app/`，系统准备流程位于 `guest/`，包校验位于 `protocol/`。机型适配的权威源码位于 [Boiler-adaptations](https://github.com/FlightDan/Boiler-adaptations)；本仓库的 `adaptations/` 是固定提交的生成快照，不作为第二份适配源码维护。

## 准备环境

使用 Windows x64 和 CPython 3.12 或更新版本。从独立检出的本仓库开始，先阅读[依赖准备文档](DEPENDENCY_BOOTSTRAP.md)，确认锁定的 .NET SDK、运行时、QEMU、Linux guest 和其他输入及其空间需求。脚本支持显式离线输入；不要依赖相邻的旧项目目录。

在仓库根目录运行：

```powershell
python scripts/bootstrap-dependencies.py --profile all
python scripts/prepare-python.py --output runtime/python
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-app.ps1 -Configuration Release
```

`prepare-python.py` 要求输出目录尚不存在；已有运行时应先核对，不能直接用此命令覆盖。下载缓存、展开后的依赖和构建输出需要预留足够空间；各脚本的输入、缓存和离线选项见其 `--help` 与[依赖准备文档](DEPENDENCY_BOOTSTRAP.md)。

如需准备可选组件，请根据固定的依赖锁使用 `scripts/prepare-decky.py` 和 `scripts/prepare-wireless.py`，再以 `scripts/prepare-integration.py` 创建新的集成目录。缺少所选载荷时，安装不能继续；源码构建成功不等于完整安装包已准备好。

## 检查

常用的源码及 Windows 应用检查入口：

```powershell
python -B -m unittest discover -s tests -p 'test_*.py'
runtime/dotnet/dotnet.exe app/bin/Release/net10.0-windows/OxpInstaller.dll --cli boiler-checks
runtime/dotnet/dotnet.exe app/bin/Release/net10.0-windows/OxpInstaller.dll --cli model-catalog-checks
```

按改动范围补充相应检查。Windows 上跳过的 Linux 专有测试需要在合适的 Linux 环境单独运行；源码测试、打包验证和真实设备验收是不同的结果。普通构建和源码检查不应操作用户磁盘、固件或真实设备。

## 包与发布资料

- [机型包与本地导入契约](DEVICE_PACKAGES.md)
- [GitHub slim 安装器发布策略](GITHUB_SLIM_INSTALLER_RELEASE_POLICY.md)
- [发布及验证记录索引](INDEX.md)

公开源码使用 `scripts/source-files.json` 白名单和独立公开检出。系统镜像、私有签名密钥、设备凭据和私人研究材料不属于公开源码或安装包。
