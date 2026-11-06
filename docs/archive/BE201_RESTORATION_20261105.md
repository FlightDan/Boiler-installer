# BE201 restoration — draft candidate

> 状态注记（2026-11-06）：本文件保留为历史草稿，内容未改写。当前 `main` 对应的
> `0.2.0-preview.20261105.11` 交付记录（`docs/MERGED_FIXES_DELIVERY_20261105.md`）
> 与构建验证 JSON 显示：两台机型的品牌包均默认包含 BE201 资源，各 9 个安装目标。
> 物理设备的 Wi-Fi/蓝牙仍未验收；以交付记录和实际验证 JSON 为准。

The canonical ONEXPLAYER model TOONs reference the shared Intel BE201 resource. The local brand builder includes the nine original firmware paths once and provides each selected model with a validated fixed resource descriptor. Old model packages remain readable; the canonical packages keep the old model Wi-Fi capability disabled to avoid competing ownership.

The existing fixed firmware handler preserves its replacement rule for c106 and fills the other files only when missing. Synthetic resource sessions exercise two-slot writes, verification and transaction-backed restoration. A selected model package containing the resource is retained with core maintenance for offline use.

## Remaining integration

The resource adapter and selection state are not connected to an actual installer or maintenance action. The available Windows Intel A840 identifier is a candidate, not proof that the target has BE201. There is no reviewed BE201 Wi-Fi/Bluetooth association or authenticated binding between those facts and the same disk and both SteamOS slots under the existing target lease. Until that exists, the destination remains unconfirmed and firmware installation is unavailable. A current-PC detection must not identify an external target disk.

The draft is not ready for BE201 deployment or release. Complete the target-bound detection and ownership/lease coordination, wire installation and retained maintenance/restore, verify model-versus-common provider and target-scoped opt-out through the real UI and execution path, and run the requested two-model lifecycle tests. Wi-Fi and Bluetooth on physical devices remain unverified.
