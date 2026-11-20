# Boiler Installer 0.2.8 slim 与 Boiler Maintenance 0.2.0+maintenance.39

Windows 安装器从[安装器发布页](https://github.com/FlightDan/Boiler-installer/releases/tag/v0.2.8)下载。解压 ZIP 后运行 `BoilerInstaller.exe`，打开机型选择，导入或拖入自己的机型包，确认包来源并选择要安装的构件。安装时同时选择通用维护器。

进入 SteamOS 后，打开 Boiler 维护器的已安装机型入口，可以验证已安装的文件、恢复受管理构件。脚本构件恢复时，按提示提供安装该构件当前版本的包解压目录。已收录的新机型还可以按已签名设备索引中的 DMI 匹配发现更新。

[维护器发布页](https://github.com/FlightDan/Boiler-installer/releases/tag/maintenance-v0.2.0-maintenance.39)提供维护运行时及签名索引，slim 安装器集成同版运行时。已有适配包和设备签名目录沿用各自的发布渠道。新机型和单部件的 PR 要求、首包构建步骤及示例见[贡献指南](CONTRIBUTING_ADAPTATIONS.md)。

本次软件验收覆盖第三方示例包的导入、已安装构件的维护，以及隔离目标上的升级和恢复。真实掌机的屏幕、手柄、启动和游戏运行结果由相应机型提交记录。

独立在空系统首装维护器，请从维护器发布页下载 `boiler-maintenance-bootstrap.39.1.pyz`。该修订支持签名更新包中为升级而声明的保留文件选项；首装使用空的文件所有权记录。同页 `boiler-maintenance-bootstrap.39.pyz` 已停用。安装器内置维护器与从旧版本升级都使用 `0.2.0+maintenance.39` 运行时。
