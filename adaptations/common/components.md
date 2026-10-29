# 跨品牌共用源码

`scripts/` 保存固定公开提交中的文件事务、共享状态、槽位切换、分区挂载、
split-lock 和重启进入 Windows 辅助源码。两个机型的 manifest 引用同一份文件，
不各自复制。原目录名和逻辑路径仍可在 `source-lock.json` 中查询。

“共用”描述源码归属，不承诺所有品牌都可直接安装。槽位切换依赖 SteamOS A/B
与官方工具；NTFS 和共享 home 需要匹配布局；split-lock 必须确认相应故障和
启动文件；Windows 重启需要实际 Windows EFI 项、授权和 Decky/桌面入口。

这些模块有的返回待部署文件，由原事务入口保存原值并恢复；不是独立的一键安装脚本。
本次未改写 `oxp3` 旧路径、日志命名或权限策略，也未重建实际安装载荷。

原 MIT 组件的上游许可证随对应 `split-lock/` 等目录保留；具体许可见各文件及仓库
`THIRD_PARTY_NOTICES.md`。
