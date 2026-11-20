# 通用微码和核显固件虚拟机检查 · 2026-11-19

## AMD 微码安装回归

QEMU 使用 SteamOS 3.10 原版启动文件复现了安装失败。`amd-ucode.img` 的 `AuthenticAMD.bin` 是 147,350 字节的普通文件，newc 头写入 `nlink=0`。原解析器只接受 `nlink=1`，在写入前拒绝了该镜像。Linux early CPIO 读取器按普通文件类型和内容边界查找数据，initramfs 仅对 `nlink > 1` 的条目使用硬链接语义。[Linux early CPIO](https://github.com/torvalds/linux/blob/master/lib/earlycpio.c#L100-L125)、[内核 initramfs 格式](https://www.kernel.org/doc/html/latest/driver-api/early-userspace/buffer-format.html#handling-of-hard-links)

`microcode_boot` 现在兼容这类原生微码条目。新增回归覆盖零链接普通条目以及硬链接、空内容的拒绝。原版 AMD 镜像与 6 个锁定容器合并后，66 个不同的补丁逐字节保留。安装器和维护运行时共用修复后的解析器，维护准备版本为 `0.2.0+maintenance.33`。

## 验收范围

在 QEMU 只读挂载已锁定的 SteamOS 3.10 根分区。Intel、AMD 微码和 12 个核显固件配置的计划均成功。该镜像已经提供 `amdgpu.ko.zst` 和 `xe.ko.zst`。其中 10 个核显配置的候选文件在根分区已有原生版本，Intel G3 与 G3 Extreme 各缺 1 项。早期归档仍按实际 initramfs 内容确定增量。

QEMU 使用两个独立测试根目录和锁定载荷完成 34 次安装、校验和恢复。通用 Intel、AMD 微码与全部 12 个核显配置均通过 A/B 槽测试。AMD 微码与 780M、Intel 微码与 Lunar Lake 固件分别验证了共用启动事务、原生固件保留和恢复顺序。缺少 `xe` 驱动或启动归档损坏的用例均在写入前报错。

独立的原版镜像用例在 A/B 槽分别安装并调用了 `.33` 维护运行时。两次都通过启动文件校验，原版 initramfs 和独立 AMD 微码镜像逐字节恢复，initramfs 权限保持一致。OXP3 和 X2 Mini 的 `xevmafix` 文件事务也通过验证和恢复，加载器正确拒绝了虚拟机的内核版本和缺少 Intel `xe` 硬件的测试环境。

Windows 微码与核显源码测试共 73 项，其中 3 项按平台条件跳过。Windows 核显选项自检通过。`.33` 本地准备包重新解包校验了全部 119 个成员和维护清单中的 109 个文件，正式发布验收单独记录。各组串口日志、测试夹具和验收结果已登记进 RAID 保留区统一清单。

通用核显配置为目标系统已有的 `amdgpu` 或 `xe` 补充固件。ONEXPLAYER 两台机型的 `xevmafix` 是独立内核模块。QEMU 离线验收区分计划、安装、文件校验、恢复和物理设备启用，GPU 硬件加载与 AMD 微码运行版本需要目标实机。OXP3 实机 Panther Lake 微码验收见 [机型微码记录](ONEXPLAYER_MICROCODE_20261119.md)。
