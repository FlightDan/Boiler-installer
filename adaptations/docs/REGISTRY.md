# 设备索引与签名

[首页](README.zh.md) · [English](REGISTRY.en.md) · [构建指南](BUILD.md)

设备索引告诉安装器有哪些机型包、从哪里获取以及如何核对。包中的 manifest 再列出具体文件和哈希。

## 两份索引

- 仓库根目录的 [device-index.json](../device-index.json) 是未签名的开发基线，当前序号为 0，包列表为空。
- [catalog-v0.1.0-preview.1](https://github.com/FlightDan/Boiler-adaptations/releases/tag/catalog-v0.1.0-preview.1) 提供已签名的 `index.json` 和 `index.sig`；该历史目录序号为 1，收录当时的 OXP3 实验包，所绑定的源码提交与当前机型 TOON 快照不同。

签名私钥由维护者保管；安装器用受信任的公钥验证索引，再核对索引中绑定的包。贡献者提交 PR 不需要签名私钥。

## 准备索引

[scripts/build-index.py](../scripts/build-index.py) 接受一个已有的机型包目录，核对 manifest 和文件清单。以下命令在 Boiler-adaptations 仓库根目录运行，生成本地开发索引；把路径改成自己的实际目录和一个尚不存在的输出文件：

```powershell
$Package = "PATH_TO_DEVICE_PACKAGE"
$Output = "PATH_TO_NEW_DEVELOPMENT_INDEX.json"
python -B scripts/build-index.py --package "$Package" --output "$Output"
```

准备发布候选时，再提供实际 ZIP、固定的发布资源地址和递增的 `--sequence`。脚本检查 ZIP 内容、manifest 和哈希，要求包声明精确的公开源码提交；单包发布候选路径针对已登记的实验性 OXP3 包。双机型候选模式使用两组 `--model PACKAGE ZIP URL`，分别提供 OXP3 和 X2 Mini 包，并校验指纹组件；还需提供已独立验签的上一份索引及其 SHA-256、递增序号、精确的适配仓库提交和版本，以及安装器检出和提交。完整参数可运行 `python -B scripts/build-index.py --help` 查看。

后续版本应先独立验证上一份签名索引，再通过 `--previous-index` 提供它；脚本不会替操作者验证该文件的签名。新序号必须递增，已有撤销记录也要保留。索引中的 `verified_at` 记录的是本地文件清单核对时间。

脚本生成的是未签名候选，不会签名、上传或改写仓库根目录的正式文件。发布时先核对并发布实际包，再按维护者签名流程签署并验证最终索引。
