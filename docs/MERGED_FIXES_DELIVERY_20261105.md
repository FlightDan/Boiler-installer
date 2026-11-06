# 双机型完整安装器修复合并与本地交付

候选安装器：`0.2.0-preview.20261105.11`。维护器：`0.2.0+maintenance.17`。

来源以 `.10` 的 `6471fbb58a38a5121e2b24ee79bc8ce096c89424` 为基线；新源码提交为 `07d40f449a0da8e3ea56cd55d326803ec0166cad`，分支 `codex/merged-fixes-20261105`。独立源码位于 `E:\Boiler-installer\merged-fixes-20261105\source`。本地未签名测试包，未推送或发布。

## 合并内容

- GM7 新装预检允许原始根镜像缺少 `/etc/default/grub.d`，仍校验已有 `/etc/default` 的路径及所有权。正式事务写入通过目录描述符建立目录；恢复原有 EFI 和配置文件字节后允许留下新建的空目录。
- 进度 JSONL 保留已有字段，新增单调累计计时 `elapsedMs`，避免墙钟跳变影响时长判断。
- 保留 `.10` 的 512 MiB 共享事务日志处理、X2 保留包信任及验证路径。未用旧工作区文件整体覆盖新版实现。
- 双机型品牌载荷继续锁定权威适配提交 `8d5053462aac703866866ca18b8f8635c5d0c007`，按 TOON 共享一份物理资源。包内没有预放第二套机型维护载荷。

## 制品

主包：`E:\Boiler-installer\merged-fixes-20261105\artifacts\boiler-installer-0.2.0-preview.20261105.11-onexplayer-full-win-x64.zip`

- 大小：721,554,756 字节。
- SHA-256：`8960c84cdf0791a9fb5cbb94d4c5ad44cace3e3a00653d8e55217429b2c64b88`。
- 精确库存及摘要使用制品现有 `build-info.json` 和相邻 `.zip.json`，没有新建另一套来源清单。
- 独立维护 ZIP 位于同一 artifacts 目录，大小 1,140,440 字节；其 88 个维护运行文件与完整安装器逐字节一致。

## 验收记录

验证资料统一位于 `E:\Boiler-installer\merged-fixes-20261105`。

| 门槛 | 状态 |
| --- | --- |
| 独立代码审查 | 通过；确认空目录残留为既有恢复边界，不修改事务格式。 |
| Linux 源码回归 | 95 项通过；覆盖 SSD、来源绑定、模型 EFI 和维护状态／恢复。`linux-regression/console.log`。 |
| Windows Release 构建 | 通过，零警告、零错误。`evidence/windows-build.log`。 |
| 最终 ZIP 实际程序 | 7 组通过：进度、通用安装器、机型目录、插件矩阵、设备、构件结果、SSD UI。`evidence/windows-suites.json`。 |
| 依赖与原始输入 | 所有 runtime profile 锁定成员通过校验；原始恢复 ZIP、3.10 rootfs 与既有输入锁一致。只读 QEMU 确认原始 rootfs 缺少 grub.d。 |
| 最终归档／双机型导入 | 通过，1,360 个库存文件及 build-info；品牌包 479 个成员，376 个共享运行成员各只存一份；两机型均能离线导入。`evidence/final-package-runtime.json`。 |
| OXP3 完整选项 A/B 集成与只读复验 | 通过；使用用户日志原始选项、锁定 SteamOS 3.10 输入和合成 GM7 目标绑定，包含 GM7、Split-lock 及完整选项生成的共享事务日志。每槽 2,087 个模型文件、45 份事务记录通过；两槽均有 GM7 独立只读证明。`guest-lifecycle/result.json`。 |
| X2 默认选项 A/B 集成 | 通过；实际打包 EXE 完成安装、独立只读复验与启动就绪检查，每槽 2,085 个模型文件、43 份事务记录通过，原始虚拟镜像前后摘要一致。`evidence/x2-full-vm.txt`。 |
| 保留维护器恢复、重应用与中断恢复 | 通过；实际安装的 88 个维护运行文件及保留机型包库存一致，A/B 恢复、重应用、复验、另一槽内容隔离均通过；在删除 B 槽修复配置后强制终止，独立进程恢复成功，待恢复记录清除。`retained-final-v2/console.log`，退出码 0。 |
| GitHub 发布／下载／签名 | 不适用；本次仅本地完整包。 |
| 实体 SSD 枚举与真实硬盘启动 | 待实机验收；虚拟机通过不等于硬件验收。 |

本机安装准备／工作目录必须放在 E: RAID；C: 可用空间不满足完整准备流程。该本机绝对路径设置不包含在便携 ZIP 中。历史候选、失败证据、原始输入和当前源码均保留。

测试工具问题单列保留：组合控制器在保存 OXP3 成功结果后，因 X2 缓存路径多写一个 `0` 退出；X2 由独立的实际打包 EXE 完成验证。维护测试等待器先到等待时限；直接执行时发现测试载荷漏带挂载辅助模块，在恢复操作前退出。补齐实际包内的 `file_transaction.py` 和 `safe_directories.py` 后，在全新 `retained-final-v2` 副本重跑；原 `retained-final` 和控制器日志均未覆盖。上述问题未修改安装器或降低产品校验。

最终机器可读验收汇总：`E:\Boiler-installer\merged-fixes-20261105\evidence\acceptance.json`。源码工作树保持干净，最终制品对应上述精确提交。
