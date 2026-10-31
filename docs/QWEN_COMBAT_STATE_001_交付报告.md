# QWEN-COMBAT-STATE-001 交付报告：战斗恢复状态机（低血退出、目标离距、脱战确认）

- 工作副本：`C:\Users\qrz\AppData\Local\Temp\opencode\pn-qwen-combat-state-001`
- 分支：`worker/combat-state-001`（基线 commit `0793a0c`，任务书声明基线 `72f1d49`/`86c5066` 均在其祖先链上）
- 最终 commit：见文末第 8 节（提交后追加）
- 全程离线：未登录游戏、未跑 WSL 实机、无网络请求、未动 `results/`（回放前后 SHA-256 逐一比对，见测试 `test_results_untouched_after_replay`）、未动凭据与父项目。
- 运行日志：全部在 `.cache/combat-state-001/`（不入库）。本 clone 的 origin 是本地裸库，按任务书禁止 push，未 push。

## 1. 完成/未完成矩阵与状态机

| 项 | 状态 | 说明 |
|---|---|---|
| A 生命真值 | 完成 | `summarize()` 新增 `health_transmitted`；`Observation` 新增 `health_transmitted: bool = True`；`observe()` 传真实值；状态机入口把未下发 health 规范化为 `None`（未知≠死亡≠低血）。纯加法，既有键/取值/顺序语义未动 |
| B 纯状态机 | 完成 | 新增 `scripts/agent_wow_combat_state_machine.py`：无网络/文件/RPC/sleep/真实时钟；状态、决策、原因码字符串与任务书逐字一致；阈值全部为 max_health 比例；低血 latch；离距区分已确认/不可确认；脱战需连续确认+最短停留+health 未降且>0，未知即重置；预算耗尽→`unresolved`+`budget_exhausted`；`dead`/`ghost` 独立终态 |
| C 离线回放取证 | 完成 | 新增 `scripts/replay_combat_state_machine.py`（CLI `--help` 可用，输出路径由调用方参数指定，`results/` 只读）与 `scripts/test_combat_state_machine_replay.py`（5 个存档全部覆盖并断言） |
| D 建议式接入 | 完成 | `fight()`/`settle()` 新增默认关闭的 `advisory` 参数，决策记录进既有证据（observations 行/phase/history 新增 `advisory_*` 字段）；默认路径输出与基线逐字段一致；本轮未启用强制执行 |

未完成/未做：无未完成的 A–D 子项。未执行 WSL 实机回归（任务书本轮不要求，留给主控）。

状态图（10 状态，终态加粗）：

```
                    ghost=True/已下发health==0
   precheck ──in_combat=True──> engaging ──in_combat=True──> in_combat ────> **dead** / **ghost**
      │  (start_sent注入)          │  ^                          │
      │ in_combat=False            │  │ in_combat未知→hold       │ health比例<低血( latch )
      v                            │  └──────────────────────────┤
  disengaging <────────────────────┴─────────────────────────────┤
      │  连续N次in_combat已知False + 停留达标 + health未降且>0      ├─> low_health_exit ──(latch，永不回in_combat/不再start_attack)
      v                                                          │        │
 **disengaged_confirmed**  <─────────────────────────────────────┤        └─ stop_attack 或 request_escape(有撤离路径)
                                                                 ├─> target_out_of_range ──(latch) stop_attack
   任意非终态 + 调用方预算耗尽 ──> **unresolved** (budget_exhausted)   │    原因码 target_out_of_range_confirmed
   目标消失(采样确证) ──> **unresolved** (target_missing, abort_unit) ┘    或 target_distance_unconfirmed
   目标HP==0(采样确证) ──> stop_attack 后进 disengaging 走确认流程
```

关键转换语义：
- 单次采样永不确认脱战；`in_combat` 或 health 未知一律重置计数且不计入（`test_single_sample_never_confirmed`、`test_unknown_*_resets_streak_*`、`test_no_sampling_means_no_confirmation`）。
- 低血/危急用独立原因码；latch 后任何后续观测不得回 `in_combat`、不得再 `start_attack`（`test_low_exit_latches_against_reentry_and_restart`）。
- 目标 HP==0 → `target_dead`、目标不在视野 → `target_missing`，二者都不记为离距（`test_dead_target_is_not_out_of_range`、`test_missing_target_is_not_out_of_range`）。
- 战斗中自身位置不广播（`agent_wow_session.py:220` 恒 `current_position_confirmed: False`），因此 `estimated_position()` 的全部来源标签都判为"距离不可确认"（`distance_confirmed_for`），不可确认时只停手、不断言目标逃走。

## 2. 修改文件与兼容性

| 文件 | 改动 | 兼容性 |
|---|---|---|
| `scripts/agent_wow_worldstate.py` | `summarize()` 在 type 3/4 分支新增 `health_transmitted = "24" in f`（与同函数既有 `stack_transmitted`/`unit_flags_transmitted` 同先例） | 纯加法键；既有键取值/顺序不变；`test_agent_wow_worldstate` 22 项原断言全过（含 incremental==full-replay 相等性） |
| `scripts/agent_wow_contract.py` | `Observation` 在 `health` 后新增 `health_transmitted: bool = True`（默认 True 兼容既有构造点） | dataclass 新字段带默认值；`to_dict()` 自动多一键；既有 checkpoint 记录为历史数据，不重读校验该键 |
| `scripts/agent_wow_session.py` | 仅 `observe()` 传 `health_transmitted=me.get('health_transmitted', True)` | 任务书允许的唯一 session 改动；`.get` 兜底使旧 summary dict 不炸 |
| `scripts/agent_wow_combat_state_machine.py`（新） | 状态机全部逻辑 | 无既有调用方 |
| `scripts/replay_combat_state_machine.py`（新） | 回放 CLI | 只读 `results/`，输出路径调用方指定 |
| `scripts/agent_wow_combat_control.py` | 新增 `advisory_decision()` 助手；`fight()` 新增默认关闭参数 `advisory/config/escape_path_planned`，advisor 每行记 `advisory_state/decision/reasons`，phase 末尾记 `advisory_final_state` | 默认 `advisory=False`：派发顺序、outcome 字符串、`start_sent_once` 语义、`finally` 恒发一次 `stop_attack` 全部未动；`test_combat_distance_boundary` 原断言通过；advisory 记录放在 `rows.append(row)` 之后，不改行序 |
| `scripts/agent_wow_session_supervisor.py` | `settle()` 新增默认关闭参数，phase 前与 health_budget 等待环内记 `advisory_pre_phase/advisory_low_hold`，结果记 `advisory_final_state` | 默认路径与基线逐字段一致（新增 import 与默认参数，无行为变化） |
| `scripts/test_combat_state_machine.py`（新） | 38 项状态机/接入单测 | 新增 |
| `scripts/test_combat_state_machine_replay.py`（新） | 8 项真实存档断言 | 新增 |

**不能证明的事项**（明确不声称）：
- 不能证明强制接入后真实战斗不会死（本轮零实机；2026-10-29 死亡复盘场景只做了离线状态机复现）。
- 不能证明 entry 3101 当次已完成 Flee For Assist 逃跑（docs/76 仍未逐包还原；回放只报"已观察/未观察"，long01 最后一步只标注距离不可确认，不断言逃走）。
- 不能证明 `AttackStop` 后多久脱战（docs/34 只证明 AttackStop≠脱战；最短停留时长 2.0s 是保守默认，未在实机标定）。
- 不能证明 run51 中 target_out_of_range latch 与当时真实 retreat 决策等价：回放里 33.3→2.6 码距离是服务器路径端点估计，非已确认位置。
- 不能证明 17376 个 self 采样之外的总体 health 下发率；66/17376 是主控既有统计的引用，本轮只直接复核了 5 个点名存档。

## 3. 实际测试命令与计数（完整输出在 `.cache/combat-state-001/`）

| 命令 | 退出码 | 结果 | 日志 |
|---|---|---|---|
| `python -m unittest discover -s scripts -p "test_*.py"`（基线，改动前） | 0 | Ran 225, OK, skipped=11 | `baseline_full_test.raw` |
| `python -m unittest discover -s scripts -p "test_combat_state_machine.py" -v`（B 完成时） | 0 | Ran 33, OK | `sm_unit_final.txt` |
| `python scripts/replay_combat_state_machine.py --help` | 0 | usage 正常 | `replay_help_final.txt` |
| `python scripts/replay_combat_state_machine.py .cache/combat-state-001/replay_final.json` | 0 | 5 存档时间线 JSON | `replay_final_run.log` + `replay_final.json` |
| `python -m unittest test_combat_state_machine_replay -v`（cwd=scripts, PYTHONPATH=scripts） | 0 | **Ran 8, OK**（关键断言逐条 ok） | `assertion_run40_long01.txt` |
| `python -m unittest discover -s scripts -p "test_combat_state_machine*.py" -v`（最终） | 0 | **Ran 46, OK**（38 单测 + 8 回放） | `final_sm_tests.txt` |
| `python -m unittest discover -s scripts -p "test_*.py"`（最终全量） | 0 | **Ran 271, failures=0, errors=0, skipped=11**（=基线 225 + 新增 46；11 个跳过是 WSL 专属，与基线一致，未为 Windows 全绿删测试） | `final_full_tests.txt` |
| `git diff --check` | 0 | 无空白错误 | 见第 8 节 |

中途真实失败（非最终态，均已定位修复并留日志）：`sm_unit_run1/run2.txt` 记录了 5 项→1 项失败过程。最后 1 项失败的真实原因是我的测试把 `steps[2]` 写成对"drive 返回列表"的错位索引（断言本身方向正确），修正测试取样方式而非削弱断言；`final_sm_tests.txt` 第一次运行的 4 个 ERROR 是新增测试漏 import `patch` 与 advisory 记录时序（行入列前未记全字段），均为测试/接入代码缺陷，修复后全过。既有测试的断言一条未删、未改。

## 4. 回放取证证据（5 个真实存档）

完整 JSON：`.cache/combat-state-001/replay_final.json`。每步含序号、包序号、状态、决策、原因码、关键观测；"未观察"字段显式列出，不以采样缺失推断状态。

**long01**（`agent_wow_bridge_quest792_long01_unit1_verify`，129 包，真实战斗 HP 108→95、目标 69→5）：
```
0 seq16 disengaging/hold_and_observe [out_of_combat_not_yet_confirmed] ic=False hp=108
1 seq32 in_combat/continue_attack ic=True hp=108 thp=69 d=2.378(未确认)
2 seq48 in_combat/continue_attack hp=105 thp=55
3 seq80 in_combat/continue_attack hp=102 thp=28
4 seq96 in_combat/continue_attack hp=98 thp=17
5 seq128 target_out_of_range/stop_attack [target_distance_unconfirmed] hp=95 thp=5 d=15.414(未确认)
6 seq129 unresolved/abort_unit [budget_exhausted]
```
时间线索引与包序号单调；退出步之后无任何 `start_attack`（本时间线全程未出现 start_attack 决策——战斗由既有 fight() 派发，状态机在回放中是咨询者）。HP/目标血量与 docs/76 记录一致。

**run40**（health 字段从未下发、max_health=70 已下发——事实 4 的回归守卫）：
```
0 seq16 disengaging/hold_and_observe [out_of_combat_not_yet_confirmed, health_unknown] hp=0(未下发) max=70
1 seq47 unresolved/abort_unit [budget_exhausted]
```
全程无 `dead`，health 归为未知。

**run51**（1 级，HP 70→53 持续掉血，target 3098）：seq96 起 `target_out_of_range/stop_attack [target_distance_unconfirmed]` 并 latch，全程无 `target_out_of_range_confirmed`、无 dead/ghost；估计距离 35.9→2.6 码但均不可确认，不断言逃走。
**run113**（2 级，target 3124 血量 71→0）：`in_combat/continue_attack`×4 → 目标血量 0、`in_combat=False` 进 `disengaging`，因确认窗口未满且在停留时长内血量上升（64→89 触发过 reset），**未**以单次采样确认脱战；无 dead/ghost。
**run116**（22 包，无攻击样本）：仅 disengaging→unresolved，不声称战斗或死亡。

两条硬断言的实际测试输出原文（摘自 `.cache/combat-state-001/assertion_run40_long01.txt`，非手写）：
```
test_long01_monotonic_timeline_no_restart_after_exit (test_combat_state_machine_replay.ReplayContractTests.test_long01_monotonic_timeline_no_restart_after_exit) ... ok
test_run40_untransmitted_health_is_unknown_never_dead (test_combat_state_machine_replay.ReplayContractTests.test_run40_untransmitted_health_is_unknown_never_dead) ... ok
...
Ran 8 tests in 1.042s
OK
```

## 5. 阈值换算表

默认比例：低血 `low_health_ratio=0.5`，危急 `critical_health_ratio=0.25`（低于该值即触发对应原因码）。

| max_health | 低血阈值(50%) | 危急阈值(25%) | 旧绝对 25 相当于 | 旧绝对 50 相当于 | 旧绝对 60 相当于 |
|---|---|---|---|---|---|
| 70（1 级） | <35 | <17.5 | 35.7%（≈旧 fight 低血线，巧合接近） | 71.4% | 85.7% |
| 89（2 级） | <44.5 | <22.25 | 28.1% | 56.2% | 67.4% |
| 108（3 级） | <54 | <27 | 23.1% | 46.3% | 55.6% |

与现有绝对阈值的差异对照（同一血量在不同等级下的含义漂移）：
- `fight()` `health<25 → health_budget`：1 级时 35.7% 才触发，3 级时 23.1% 才触发——同一数字随等级漂移 12.6 个百分点；30 血在 70 上限是 43%（已低血），在 108 上限是 28%（仍低血但未危急）。
- `fight()` `health<50 且不在战斗 → preconditions_not_met`、`effect_gate health<50`：70 上限时 71.4%（几乎永远不满足），108 上限时 46.3%——同一绝对值在三档上限下语义完全不同。
- `task_boundary health<60`：55.6%–85.7% 漂移；`retreat minimum_health=50` 同理。
- 比例阈值下三档行为一致；换算表使"低血"在任何等级都是同一风险等级。

## 6. 若强制接入（状态机真正否决派发），会被改变的现有分支

1. `fight()` `me['health']==0 → self_dead`（`agent_wow_combat_control.py` 现 :60）：health 未下发时旧代码判死；强制接入后改判 `health_unknown`，run40 类档案不再误报 self_dead。
2. `fight()` `health<25 → health_budget`：改为比例阈值+危急独立原因码；1 级角色触发点从 25 变为 35（更早），3 级从 25 变为 54（更早且更保守）。
3. `fight()` `health<50 且不在战斗 → preconditions_not_met`：比例化后 3 级需 <54（更严），1 级 <35（更松）。
4. `fight()` `sent and distance>5 → target_left_melee`：强制接入后该距离恒为不可确认（战斗不广播自身位置），outcome 语义应变为 `target_distance_unconfirmed` 停手而非断言目标逃走。
5. `fight()` 低血后被动 `time.sleep+drain` 继续循环：低血 latch 后状态机不再给 `continue_attack`，循环应停手观察，杜绝 2026-10-29 复盘中"低血仍长采样"。
6. `settle()` `in_combat is True` 期间再次调用 `fight()`（可再派 `start_attack`）：latch 的 `low_health_exit/target_out_of_range` 会否决再次 `start_attack`。
7. `settle()` `health_budget` 被动等待环：改为按状态机恢复分支（hold_and_observe/request_escape），且未知 health 不得当作 >0 放行。
8. `settle()` `safe_to_logout = 单次采样 in_combat is False`（现 :52）：改为 `disengaged_confirmed`（连续 N 次+停留+health 未降且已知>0）才可登出。
9. `finalize.py` `health<=0` 判死、`in_combat is False` 单次登出依据：同样受 A/脱战确认规则改写（本轮未改该文件，属强制接入轮）。
10. `effect_gate health<50`、`task_boundary health<60`、`retreat minimum_health=50`：全部换算为比例，触发点按上表移动。
11. 对外契约影响：`wsl_task_runner.py:107`/`agent_wow_batch.py:72` 依赖的 `outcome=='target_dead'` 不变；但新增 outcome（如 `target_distance_unconfirmed` 取代 `target_left_melee`）需要主控决定是否保留旧字符串别名。

## 7. 仍存在的限制与未执行实验

- 未跑 WSL 全量回归与任何实机（任务书禁止本轮登录/实机）；225+46 只是离线数字，不等于战斗恢复成功。
- 最短停留时长 2.0s、确认次数 3、max_range 5.0 码未实机标定；5.0 沿用了旧 fight 的绝对值。
- `Observation.health_transmitted` 默认 True 是为兼容既有构造点；未来新调用方若忘传值会把未下发当已下发，需要主控在强制接入轮决定是否收紧默认。
- 回放的目标锁定启发式（victim/facing GUID + 伤害日志粘滞）是确定性代理，不等同于当时 fight() 的目标选择；run51/long01 已核对一致，其余档案未逐一核对。
- `summarize()` 对 type 3/4 之外的对象不产 `health_transmitted`；`agent_wow_finalize.py` 的 `health<=0` 仍读旧语义（本轮范围外，已列入第 6 节清单）。
- 未做：真实时钟下的 settle 行为差异实验、run40 场景的强制接入端到端实验、跨等级升级瞬间（max_health 变化）的比例阈值连续性实机验证。
- 任务外问题（仅建议，未顺手改）：`wsl_bridge_probe.py`/`verify_789_chain.py` 等 WSL 侧脚本若要用 A 项语义需自行适配；`docs/88` 的只读契约结论未受本轮影响。

## 8. Git status 与最终 commit

提交前 `git status --short`（全部为本任务允许文件，无 `.cache`、无 `results/` 变更）：
```
 M scripts/agent_wow_combat_control.py
 M scripts/agent_wow_contract.py
 M scripts/agent_wow_session.py
 M scripts/agent_wow_session_supervisor.py
 M scripts/agent_wow_worldstate.py
?? scripts/agent_wow_combat_state_machine.py
?? scripts/replay_combat_state_machine.py
?? scripts/test_combat_state_machine.py
?? scripts/test_combat_state_machine_replay.py
```
`git diff --check`：退出码 0，无输出。

最终 commit 与提交后状态（提交后追加，见 `.cache/combat-state-001/git_after_commit.txt`）：

最终 commit（分支 worker/combat-state-001，提交后 git status 为 clean）：

`
70da10c combat-state-001: 战斗恢复状态机整块交付（低血退出、目标离距、脱战确认）
0793a0c docs: hand off combat recovery state machine as one bounded worker task
72f1d49 docs: add controller acceptance notes for reliability task
`

提交后 git status --short：(clean)。git diff --check 退出码 0。提交信息以 UTF-8 字节存于 Git（Windows 控制台按 GBK 渲染会显示乱码，git log 原始字节已核验为正确 UTF-8）。未 push、未 merge/rebase/reset/clean；.cache 未入库。
