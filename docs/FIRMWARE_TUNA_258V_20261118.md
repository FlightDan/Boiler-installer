# 清华固件源与 Core Ultra 7 258V 核显（2026-11-18）

通用固件下载依次尝试清华 TUNA、GitLab 和 Google Gitiles。清华源通过 Git smart HTTP 请求清单中已锁定的单个 blob，使用安装器已有 Python 标准库。响应限定为单个完整 blob，核对 PACK 校验、解压长度以及原有文件大小、SHA-256 和 Git blob 标识。来源不可用时自动回退，完整性校验失败时停止。已有缓存继续按锁校验。

258V 核显新增 `graphics:intel-258v`。Intel 官方资料将该处理器对应到 Arc 140V，Linux 上游将其平台归入 Lunar Lake。手动选项通过原有核显选择与确认方案入口进入事务。本机自动建议同时要求 258V 处理器名称、Lunar Lake 显卡 PCI 身份和相容设备名称。Windows 枚举结果仍须由用户核对安装目标。

核显基线升至 `0.1.0-baseline.2`，继续固定上游提交 `2ab4ce216003ee2c2396c69e19455e4379b0e1f0`。正式生成器生成四个固件及相应许可证的大小、SHA-256 和 Git 对象记录。

- `xe/lnl_guc_70.bin`
- `xe/lnl_huc.bin`
- `xe/lnl_gsc_1.bin`
- `i915/xe2lpd_dmc.bin`

安装沿用已有文件保留、A/B 槽规划、目标 xe 驱动检查、启动镜像复核和维护事务。原生固件与恢复边界通过现有事务处理，实机运行状态继续记录为待验证。

通用选项页顶部显示联网提醒，说明清华优先及自动回退。三个来源均不可用时，失败摘要突出显示原因和检查 Windows 网络、DNS 或代理后重试的办法。

此前“指纹测试缺项”来自 `app/BoilerCustomBuildChecks.cs` 的测试映射字典。设备识别已有 BF63112 与 FTE3600 两类观察结果，测试字典漏掉对应键。本次补齐空映射，与界面中尚未关联可安装构件的状态一致。

受锁固件输入、原组件载荷备份、最终组件载荷、实际清华下载和离线缓存校验证据保留在本机 RAID 的 `Boiler-adaptations/artifacts/firmware-258v-20261118/`。保留区 README 和统一 MANIFEST 记录位置、来源、大小与校验值。

本地活动组件载荷通过 `scripts/prepare-integration.py` 重建，组件目录包含新核显元数据，并重建保留维护源码载荷。维护与恢复沿用持久化事务清单。发布打包、签名、公开下载及 258V 实机安装、启动、升级和恢复验收待执行。

验收记录如下。

- 完整 Python 源码回归通过，共 1065 项，98 项按平台或环境条件跳过。基线版本迁移用例另行复核通过。
- 固件和核显专项源码测试通过，共 101 项，Windows 上合计跳过 6 项平台相关测试。C: 临时目录出现过原子目录重命名访问拒绝，转到 RAID 专用临时目录后通过。
- Windows Release 编译通过，无警告。无线选项、核显选项、自定义构建、基础安装器和机型目录五组 CLI 检查通过。
- 已查看中文与英文的联网提醒和失败摘要截图。258V 正常选择、确认方案、语言切换和错误提示检查通过。
- 实际清华下载四个固件、两份许可证和 WHENCE 均通过锁校验。最终清单完成无网络缓存生成。
- 受控 Linux 虚拟机中的 13 项组件安装与恢复检查通过，包含核显原生链接、压缩固件、启动镜像和中断事务恢复。
- 258V 实机安装、启动与硬件运行验收仍需目标设备。发布验收需另行准备新版本包、签名、公开下载核验和升级恢复验证。

相关来源如下。

- https://mirrors.tuna.tsinghua.edu.cn/help/linux-firmware.git/
- https://www.intel.com/content/www/us/en/products/sku/240957/intel-core-ultra-7-processor-258v-12m-cache-up-to-4-80-ghz/specifications.html
- https://github.com/torvalds/linux/blob/v6.17/include/drm/intel/pciids.h
- https://github.com/torvalds/linux/blob/v6.17/drivers/gpu/drm/xe/xe_uc_fw.c

## 同日家族范围修正

当前选项改为普通的“Intel Lunar Lake · Arc 130V / 140V”。同组 GuC、HuC、GSC 和显示固件由内核按 Lunar Lake 平台选择，支持清单覆盖 Core Ultra 5 的 226V、228V、236V、238V，Core Ultra 7 的 256V、258V、266V、268V，以及 Core Ultra 9 的 288V。自动建议按 Lunar Lake 显卡 PCI 身份匹配，处理器型号仅作为搜索别名。

四个固件和三份许可证记录与前一版本逐项一致。为兼容已经保存的方案，继续沿用原内部 profile ID。家族元数据记录 7、8 两种 Xe 核心数量，既有单一数量值继续有效。核显基线升至 `0.1.0-baseline.3`，通过正式生成器更新元数据和活动组件载荷。

“核显型号”使用普通设置标题，顶部醒目提示统一说明部分构件可能需要联网。相关源码检查通过，共 52 项，Windows 上跳过 3 项平台相关检查。固件安装、维护与恢复继续使用已有事务，实机运行验收另行进行。

本次家族修正的 Windows Release 编译、核显匹配与搜索、自定义构建、基础安装器和机型目录检查通过。已查看中英文家族选项与普通联网提示截图。活动组件载荷与保留输出逐文件一致，旧版载荷作为历史记录保留。家族修正材料位于 RAID 保留区的 `artifacts/firmware-lunar-lake-20261118/`。

家族资料见 https://cdrdv2-public.intel.com/828731/Intel%20Core%20Ultra%20Processors%20Series%202%20-%20LNL%20Quick%20Reference%20Guide%20v2.0.pdf 。
