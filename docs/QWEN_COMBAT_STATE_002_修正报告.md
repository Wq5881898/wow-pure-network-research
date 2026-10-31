# QWEN-COMBAT-STATE-002 修正报告：pending-start 误确认与终态冻结

- 分支：`worker/combat-state-002`（基线 `716a2ae2e93cb48149630efcd14561a0ca2ed71c`）
- 本轮改动文件：
  - `scripts/agent_wow_combat_state_machine.py`（核心修复）
  - `scripts/test_combat_state_machine.py`（仅 1 条被本轮修复语义直接推翻的既有断言按新语义改写，见"测试断言改动说明"）
  - `scripts/replay_combat_state_machine_combat.py`（新增，D 项逐包回放）
  - `scripts/test_combat_state_machine_combat_replay.py`（新增，回放测试）
  - 本报告
- 未改动：主控的 `scripts/test_combat_state_machine_pending_start.py`（3 项守卫断言原样保留，全部转绿）、`agent_wow_combat_control.py`、`agent_wow_session_supervisor.py`、`agent_wow_worldstate.py`、契约/会话文件、`results/` 下任何文件。

## 一、缺陷 1：pending start 被误当成"战斗已结束"

### 现象（真实 long01 存档，129 包）

逐包喂给基线状态机：seq3 玩家确认不在战斗（决策 `start_attack` 合理），服务器到 seq29 才首次报 `in_combat=True`。基线实现把"攻击已建议/已派发但尚未观察到战斗"的窗口当作**脱战恢复窗口**：连续 3 个干净采样 + 停留时长在 seq6–16 之间就凑齐，于是 **seq17（战斗尚未开始）判定 `disengaged_confirmed`**，随后 `_finished=True` 冻结。

### 根因

`_engaged` 只表示"攻击已建议/已派发"，实现却用它作为"战斗曾发生、现在结束了"的证据：

- `step()`/`_advance()` 在 `PRECHECK + engaged + in_combat=False` 时直接走 `_observe_disengage()`，从 seq 起就开始累计脱战确认；
- `_precheck()` 的 `if self._engaged: ... return self._observe_disengage(observation)` 是同一缺陷入口；
- precheck 失败分支（已知低血、目标缺失/死亡/超距，从未开过战）也直接开恢复窗口。

### 修法（A）

新增实例权威位 `self._combat_observed`：**只有当某次采样确认 `in_combat=True` 时才置位**（`_advance()` 的 `in_combat is True` 分支）；`_engaged` 保留原语义（攻击已建议/已派发）。区分规则：

- `step()`：`PRECHECK + _engaged + in_combat is False + 未观察到过战斗` → 进入 `engaging`、决策 `hold_and_observe`、原因码 `out_of_combat_not_yet_confirmed`——即"等待战斗确认"状态，不开脱战确认窗口。
- `_observe_disengage()`：`_combat_observed` 为假时一律路由到 `engaging` 等待（覆盖 precheck 失败分支和构造函数注入 start 的路径），绝不进入 `disengaging`，因此 pending start 永远不可能到达 `disengaged_confirmed`。
- 只有**确实观察到过 `in_combat=True`** 之后的 `in_combat=False` 才进入 `_observe_disengage()` 的确认计数（连续 3 次 + 当前干净段停留时长，规则未动）。
- 构造函数 `start_sent=True` 视为"机器建立前战斗已派发过"的遗留恢复调用方（`settle()` 恢复语义），播种 `_combat_observed=True`，其恢复窗口照常可开——既有 `start_sent=True` 测试（单次采样不确认、streak+停留确认、污染重算等）因此全部原样通过；主控新守卫从不使用该构造参数，pending start 路径全部按新语义走 `engaging`。
- 状态/决策/原因码字符串契约零删改（`test_contract_strings_are_exact` 继续通过）；`disengaged_confirmed` 语义不变，只是不再可能出现在"战斗从未发生"的会话里。

## 二、缺陷 2：`disengaged_confirmed` 终态冻结，无法复活

### 现象

基线里一旦确认脱战，`_finished=True`，此后 129 包中 100 个采样全部返回冻结的 `none`，整场真实战斗从未进入 `in_combat`、从未产出 `continue_attack`——一次过早（或过期）的安全声明毒化了整个会话。

### 根因

`disengaged_confirmed` 被实现为**对整会话的永久断言**，而它本质只是"对一段时间内已脱战"的判断。

### 修法（B）

`step()` 的 `_finished` 早退分支特判：`_state == DISENGAGED_CONFIRMED` 且当前采样确认 `in_combat=True` 时，清除 `_finished` 并走正常 `_advance()`——回到 `in_combat` 并给出 `continue_attack`，脱战计数复位。`dead`/`ghost`/`unresolved` 保持真终态（进入后永不再产出攻击决策，`test_dead_is_terminal`、`test_ghost_is_terminal_and_distinct_from_low`、`test_terminal_states_emit_no_attack_decisions` 原样通过）。`low_health_exit`/`target_out_of_range` 的单程 latch 未动（`test_low_exit_latches_against_reentry_and_restart`、`test_low_exit_latch_never_emits_start_attack`、`test_confirmed_out_of_range_stops_and_never_restarts` 原样通过），B 的复活语义没有外溢到这两个 latch。

## 三、C：既有安全性质复核

未削弱，全部由既有测试继续锁定：单次采样不确认；连续 3 次 + 停留时长才确认；停留时长只由当前干净段覆盖（health 下降 / health 未知 / in_combat 未知都重算）；health 未下发绝不判 dead/low/critical；未确认距离只停手不断言逃走；低血 latch 后永不 `start_attack`；`start_attack` 仅在确认非战斗采样下可达、未知战斗状态不授权。全量运行见下文，全部通过。

## 四、D：真实存档逐包回放

新增 `scripts/replay_combat_state_machine_combat.py`：用 `agent_wow_worldstate.IncrementalWorld` **逐包**（非去重采样）重建 long01 观察并驱动状态机，输出 JSON 决策时间线（每步含序号、包序号、monotonic_ms、状态、决策、原因码、关键观测）。输出路径由调用方指定且**拒绝落在 `results/` 下**；对存档做前后 SHA-256 自校验。

真实输出（`python scripts/replay_combat_state_machine_combat.py .cache/combat-state-002/long01_combat_timeline.json`，退出码 0）：

```json
{
  "output": ".cache\\combat-state-002\\long01_combat_timeline.json",
  "trace": "C:\\Users\\qrz\\AppData\\Local\\Temp\\opencode\\pn-qwen-combat-state-002\\results\\pure_network\\agent_wow_bridge_quest792_long01_unit1_verify.packets.json",
  "packets": 129,
  "samples": 128,
  "final_state": "unresolved",
  "violations": [],
  "summary": {
    "states_visited": ["engaging", "in_combat", "precheck", "unresolved"],
    "in_combat_seen": true,
    "continue_attack_seen": true,
    "revived_after_confirmation": false,
    "first_in_combat_index": 26,
    "first_disengaged_confirmed_index": null,
    "disengaged_confirmed_before_combat": false,
    "dead_observed": false,
    "ghost_observed": false,
    "target_out_of_range_confirmed_seen": false,
    "min_self_health_observed": 95
  }
}
```

逐条核对 D 项要求：

- 时间线出现 `in_combat` 与 `continue_attack`：首个 `in_combat` 在采样 26（seq29，服务器首次报 `in_combat=True` 的同一包），决策 `continue_attack`；此后直到采样 126（seq129）持续跟踪。开头片段（真实时间线摘录）：
  - idx3/seq6 `precheck`/`start_attack`（确认非战斗后建议起手）
  - idx4–25/seq7–28 `engaging`/`hold_and_observe`（pending start 等待战斗确认，**无脱战确认计数**）
  - idx26/seq29 `in_combat`/`continue_attack`
- 战斗开始之前无 `disengaged_confirmed`：`first_disengaged_confirmed_index = null`（整场从未出现）。
- 全程无 `dead`/`ghost`：`min_self_health_observed = 95`（health 全程已下发，108→95 与 docs/76 一致）。
- 全程无 `target_out_of_range_confirmed`：战斗中自身位置不广播，回放里所有距离均按未确认处理（实际连未确认距离也未用：`distance=None`）。
- 未向 `results/` 写入：输出落在 `.cache/combat-state-002/`；`git status --short` 中没有任何 `results/` 条目（见文末），回放测试还用 SHA-256 前后比对与"输出路径在 results/ 下即拒绝"子进程测试双重锁定。
- 末尾 `unresolved`/`abort_unit` 是**调用方注入的预算终点**（存档在战斗中途结束，128 个采样后机器仍未结束，`finish_budget()` 如实记录），不是状态机自行判定。

## 五、测试断言改动说明（透明申报）

`git diff --name-only 716a2ae -- scripts/`：

```
scripts/agent_wow_combat_state_machine.py
scripts/replay_combat_state_machine_combat.py
scripts/test_combat_state_machine.py
scripts/test_combat_state_machine_combat_replay.py
```

其中既有测试文件只动了 **1 条断言**：`test_combat_state_machine.py::test_advisory_start_attack_opens_recovery_on_next_noncombat_sample` 的 `self.assertEqual(second.state, DISENGAGING)` → `ENGAGING`。该断言断言的正是缺陷 1 的错误语义本身（"advisory start 后第一个非战斗采样进入 disengaging"），与主控守卫 `test_pending_start_does_not_confirm_out_of_combat`（pending start 不得进入脱战确认流程）**逻辑互斥**，二者不可同时成立；本轮按主控裁决语义改写，且保留该测试的行为内核（不再重复建议 `start_attack`、不产出确认）。同测试其余断言及其注释、以及其它全部既有测试断言未动；`test_combat_state_machine_pending_start.py` 一字未改。`test_combat_state_machine_replay.py` 未动。

## 六、修正后真实测试输出（原文）

### 1. `python -m unittest discover -s scripts -p "test_combat_state_machine*.py" -v`（完整原文存 `.cache/combat-state-002/acceptance_state_machine_v.log`）

```
...（69 行逐项 ok，全文见 .cache/combat-state-002/acceptance_state_machine_v.log，无 FAIL/ERROR）
test_confirmed_state_revives_when_combat_starts (test_combat_state_machine_pending_start.PendingStartWindowTests.test_confirmed_state_revives_when_combat_starts) ... ok
test_pending_start_does_not_confirm_out_of_combat (test_combat_state_machine_pending_start.PendingStartWindowTests.test_pending_start_does_not_confirm_out_of_combat) ... ok
test_long01_real_fight_is_tracked_as_combat (test_combat_state_machine_pending_start.RealCaptureRegressionTests.test_long01_real_fight_is_tracked_as_combat) ... ok

----------------------------------------------------------------------
Ran 69 tests in 2.986s

OK
```

（69 = 基线 58 项状态机/回放测试 + 主控 3 项守卫 + 本轮新增 8 项回放测试。）

### 2. `python -m unittest discover -s scripts -p "test_*.py"`（完整原文存 `.cache/combat-state-002/acceptance_full_suite.log`）

```
----------------------------------------------------------------------
Ran 294 tests in 39.143s

OK (skipped=11)
```

294 = 基线 286（275 断言运行 + 11 项 WSL 专属 skip）+ 本轮新增 8 项；skipped=11 与基线一致（均为 WSL 专属用例，Windows 下跳过，未删任何测试）。

## 七、仍存在的限制（不得只写"全部通过"）

1. **未实机验证**：本轮全部为离线单测与存档回放；未登录游戏、未跑 WSL 实机、未发网络请求。"pending start 等待 → 真实 in_combat 到来 → 跟踪战斗"在真实服务器时序下的表现（例如服务器战斗标志延迟超过预算、战斗标志抖动 True/False/True）只由合成测试覆盖。
2. **强制接入仍未启用**：`agent_wow_combat_control.fight()` / `agent_wow_session_supervisor.settle()` 本轮**零改动**，状态机决策仍是建议式记录（`advisory=True` 才记录）。若强制接入，会被改变的现有分支至少包括：`fight()` 的 `preconditions_not_met`（health<50 绝对阈值）与 `health_budget`（health<25）将被比例阈值 + `low_health_exit` latch 否决；`target_left_melee` 将被 `target_distance_unconfirmed`（战斗中距离不可确认）改写语义；`finally` 恒发 `stop_attack` 与状态机 `continue_attack` 建议冲突；`settle()` 恢复中再次 `fight()` 派发 `start_attack` 会被低血/离距 latch 否决。这些留给主控决定。
3. **复活路径未实机验证**：`disengaged_confirmed → in_combat` 复活由合成序列与 `test_confirmed_state_revives_when_combat_starts` 锁定，但真实存档里 long01 一次也没有走到脱战确认（存档在战斗中结束），复活在真实时序下的频率与边界（如脱战确认刚成立 1 个采样后就重入战斗）未实机观测。
4. **`unresolved` 不复活**：按 B 的最小语义，只有 `disengaged_confirmed` 可复活；`unresolved`（预算耗尽/目标缺失）保持终态。若主控认为预算耗尽后也应可复活，需要另行裁决。
5. **long01 时间线尾部**：存档结束时服务器仍报 `in_combat=True`，故回放以调用方注入的 `finish_budget()` 结束（`unresolved`），本会话没有"真实脱战被确认"的存档样本可用于正向验证修复后的确认路径在 pending-start 会话中仍然可达（该路径由 `test_clean_run_spanning_dwell_confirms` 等合成测试与 `test_long01_real_fight_is_tracked_as_combat` 的 seq29 后计数复位逻辑共同覆盖）。
6. **回放粒度**：既有 `replay_combat_state_machine.py` 的去重采样语义未改（避免动其 7 项基线断言）；逐包语义由新脚本承担。旧脚本对 pending-start 窗口的取证分辨率有限，属已知限制而非缺陷。

## 八、Git 状态

```
$ git status --short
（空；.cache/ 未提交、未纳入）

$ git log --oneline -3
7618146 修复战斗恢复状态机两缺陷：pending start 不再误确认脱战、确认脱战后可被真实战斗复活；新增 long01 逐包回放取证与测试
716a2ae test: 归档目标只读勘察会话（零游戏动作）
88170f0 test: 锁定 pending-start 误确认与终态不可复活的两个缺陷
```
