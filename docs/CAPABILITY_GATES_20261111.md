# 能力屏蔽闸门的归属重构 — 2026-11-11（2026-11-12 修订）

## 2026-11-12 修订：`oxp3:wifi` 退出闸门表

上一版把 `oxp3:wifi` 也放进了 `GATES`，结果是**同一条规则有了两处定义**：
`protocol/capability_gates.py` 一处，`protocol/manifest.py` 的资源/handler 互斥检查
（`resource:intel-be201-firmware` 与可用的 `oxp3:wifi` 不可共存）另一处，canonical 侧的
`build-fingerprint-overlay.py` 与 `build-index.py` 又各抄一遍。

BE201 的真实归属是**资源模型**：`resource:intel-be201-firmware` 承载 c106 的
`missing-only` 策略与事务恢复。旧版 handler 只是因为包里声明了该资源而必须关闭，
这是归属问题，不是政策问题。因此：

- `GATES` 只保留 `oxp3:gm7-resume`。
- `finalize-canonical-model.py` 改为**从包声明推导归属**：只有 `resources` 或
  `common_device_sources.resources` 里真的声明了 `resource:intel-be201-firmware`，
  才关闭 `oxp3:wifi`；没有声明就不动它。
- `protocol/manifest.py` 的资源/ handler 互斥检查**保留不动**，它是拆除闸门后唯一
  剩下的防双写边界。
- canonical 侧删除 `build-fingerprint-overlay.py` 与 `build-index.py` 里重复的
  wifi 断言；指数校验只保留 decky / contract / fingerprint 契约。

**同时修复的基座缺陷。** `RELEASE_BASES['device:x2-mini']` 曾钉在 `b790d8f4`
（2026.11.10.1）。逐包核对后发现它是 `build-model-recovery-only.py` 的 4 组件中间产物，
**没有顶层 `resources` 也没有 `common_device_sources`**，即 BE201 在包里彻底没有归属。
`finalize-canonical-model.py` 还缺一步：它只把 BE201 作为源材料放进 `canonical-common/`，
从未写出可安装的 `common/network/intel/be201/resource.toon` 与顶层 `resources`。
本轮补上 `materialize_resources()`，复用 `scripts/resources/build_wifi_pool.build`。

## 问题

X2 机型包无法重新发布，卡在 `build-fingerprint-overlay.py` 发布模式的 BE201 安全闸门
（要求 `oxp3:wifi` 为 `available: False, default: False`）。根因不是规则本身错，而是
**这条不变量只有一个历史绑定的一次性链能产生**，链已无法重放。

四处结构性缺陷：

1. **判定绑在旁证上。** `finalize-canonical-model.py` 用 `canonical=('source_commit' in proof)`
   决定是否屏蔽。安全选择取决于 preparation proof 里恰好有没有某个字段。
2. **非 canonical 分支是 fail-open。** 它把 `default` 设为 `True` 并把 `oxp3:wifi`
   加进 selection，直接产出会写 BE201 的 base。
3. **应用器不幂等、且不接受已屏蔽的输入。** 只能"碰巧在正确入参上跑一次"。
   GM7 resume 是同一模式的第二个实例。
4. **没有应用记录，只有最终值。** "刻意按政策屏蔽"与"因别的原因恰好为 False"无法区分。

## 本轮完成

### installer 侧（`C:\Boiler-installer`，已改并验证）

| 文件 | 改动 |
| --- | --- |
| `protocol/capability_gates.py`（新） | 唯一的策略表 `GATES`（`oxp3:wifi`、`oxp3:gm7-resume`）、幂等 `apply_gates()`、逐条报错的 `violations()`、`assert_gates()`、证据结构校验 |
| `protocol/manifest.py` | 校验 `capability_gates` 结构；命名了闸门的包必须真的守住闸门 |
| `maintenance/engine.py` | `RUNTIME_FILES` 登记新模块（未登记会被 `runtime_files()` 拒绝） |
| `scripts/apply-capability-gates.py`（新） | 幂等阶段应用器，走校验过的锚定摘要 |
| `scripts/finalize-canonical-model.py` | 删除 `canonical=` 参数与 fail-open 分支；GM7 本地断言移入共享表；产出前 `assert_gates` |
| `scripts/build-model-recovery-only.py` | 产出前 `apply_gates` + `assert_gates` |
| `scripts/build-onexplayer-brand.py` | 品牌候选继承基座前重新应用并断言闸门 |
| `scripts/source-files.json` | 登记两个新文件（保持排序） |
| `tests/test_capability_gates.py`（新） | 23 项 |
| `tests/test_chain_capability_gates.py`（新） | 7 项链级回归 |
| `tests/test_finalize_canonical_model.py` | 新增 3 项，改为断言"没有 fail-open 分支" |

**证据模式的设计取舍。** `require_evidence` 区分两个正当受众：已发布包无法追溯获得证据
（改其字节不可能），所以**加载**不要求；但**产出**包必须证明是哪条策略关掉了 handler。
链调用 `require_evidence=True`，校验器默认不要求。已核对缓存中全部 45 个 OXP3/X2 包
均无 `capability_gates`，此设计避免了让历史包全部不可加载。

### 未完成：canonical 侧（必须提交到 `FlightDan/Boiler-adaptations`）

本轮曾尝试直接改 `adaptations/` 下的三个文件，**被导出自检正确拒绝**（13 个错误，
`Canonical source differs from inventory`），已全部回滚，快照恢复为字节精确。
`adaptations/` 是生成快照，不是作者侧，规则有效。

需要在 canonical 仓库完成的等价改动（未提交，未推送）：

1. `scripts/build-fingerprint-overlay.py`
   - `load_protocol()` 额外导入 `from protocol.capability_gates import violations as gate_problems`、`evidence_row as gate_evidence`
   - `build()` 的 release 分支用 `gate_problems(package)` 断言，并把原先挤在一条
     `ValueError` 里的五个条件拆成各自的具名错误
   - build-provenance 增加 `'capability_gates':[gate_evidence(row['id']) ...]`
2. `scripts/build-index.py` 的 `validate_fingerprint_release()` 追加
   `assert_gates(manifest, 'the indexed model package')`（在 `sys.path` 注入之后，函数级导入）
3. `tests/test_fingerprint_overlay.py` 夹具补上 `capability_gates` 证据块，
   断言改为具名错误消息，并断言 provenance 含证据
4. **`brands/onexplayer/oxp3/bridge/selection.example.json`**：当前 `repairs` 含
   `oxp3:wifi`，会把屏蔽后的能力带进选择。`build.py:340` 要求发布选择等于它。
   已在本地验证：新的 `configure_wifi_selection` 会拒绝它
   （`Reviewed example selection names an unavailable capability`）。
   需要从该列表移除 `oxp3:wifi`。

改完后重跑 `adaptations/model-source-files.json` 的哈希，重新导出快照。

## 已知 blocker

- `RELEASE_BASES['device:x2-mini'] = 'b790d8f4…'` **不是本地改动**，它已在 canonical
  提交 `0c2f1ee`（在 `72bf401` 里）。该 pin 指向一个 `available: True` 的基座，
  即常量当前指向一个违反闸门的候选。canonical 侧改动落地后，应重新 pin 一个
  带闸门证据的候选摘要。
- `selection.example.json` 的 `oxp3:wifi` 未移除前，X2/OXP3 的 canonical 重建链
  仍会失败（这是正确行为，不是回归）。
- 设备索引 sequence 3、双机型品牌包仍依赖重新发布的 X2 包，未建。

## 验收边界

installer 侧全量 Python 套件：`1044 tests, errors=1, skipped=98`。该唯一错误是
`test_provider_inventory_uses_existing_verifier_and_lock`，与改动前基线
（`1011 tests, errors=1`）完全相同的 Windows 8.3 短路径问题
（临时目录 `ADMINI~1` 与 `Administrator` 解析不一致），非本轮引入。
本轮新增/更新 33 项测试。`adaptations/` 快照已核对为字节精确（0 处差异）。

**未**上传任何包，**未**签名索引，**未**接触设备，**未**构建 Windows 安装器，
**未**验证 Linux 安装/维护/恢复。物理硬件验收仍全部未完成。canonical 侧改动未提交。
