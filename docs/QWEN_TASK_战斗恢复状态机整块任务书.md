# QWEN-COMBAT-STATE-001：战斗恢复状态机（低血退出、目标离距、脱战确认）整块交付

**本文件是新窗口唯一必读交接，不需要读取历史聊天或逐篇阅读docs。**
角色：你是离线开发执行者，负责完成一个完整模块；主控窗口负责协议语义、实机动作和事后集中验收。用户已同意采用独立克隆开发，不共享主工作区。

## 一、目标和当前事实

本项目是AzerothCore纯网络路线，与客户端截图/Lua路线独立。本轮**不碰游戏**，只做离线状态机。

- 主控工程：`C:\APP\opencode\project\WoW自动化预研\pure-network`
- 远端：`https://github.com/Wq5881898/wow-pure-network-research`
- 交接基线：`72f1d4921632be8c38368eb3c2d85fdbe74f6e5d`（含本任务书的后续提交；基线含任务书声明的 `86c5066`）。
- 基线Windows全量225项通过（11项WSL专属跳过）、WSL全量225项通过；这是基线记录，你须报告自己的实际数字。
- 关联历史教训（客户端路线，不在本轮范围，但约束本轮设计）：2026-10-29战斗死亡复盘——**3次固定施法后目标仍有3生命，玩家采样时44/52且在战斗，驱动却进入长聊天采样/reload/人工诊断链路，最终死亡**。因此本轮要求：战斗中**禁止任何诊断链**，必须进入**有界、预先规划**的恢复分支；逐次确认同一GUID的目标/射程；**任务失败或停手不等于脱战**。

### 已核实的事实（主控已复核，可直接引用；行号为基线）

1. `scripts/agent_wow_combat_control.py:26-61` 是当前唯一的战斗控制器 `fight()`。它在单个 `while` 循环里混合了朝向、攻击启动、距离判断和健康判断，阈值全部是**绝对血量**：`health<25` → `health_budget`（:56）；`health<50` 且不在战斗 → `preconditions_not_met`（:52）；`distance>5` → `target_left_melee`（:42-45）。`finally` 恒发一次 `bridge.stop_attack`（:59）。
2. 同样的绝对血量阈值散落在多处：`scripts/agent_wow_effect_gate.py:13` 用 `health<50`；`scripts/agent_wow_task_boundary.py:11` 用 `health<60`；`scripts/agent_wow_retreat.py:79` 的 `minimum_health` 默认50。而本机角色**等级不同则max_health不同**：主控对379个存档trace统计，self的max_health分布为 **70（1级，62个trace）/ 89（2级，91个）/ 108（3级，226个）**。因此绝对阈值随等级漂移，30点血在70上限时是43%，在108上限时是28%。
3. `scripts/agent_wow_worldstate.py:310` 用 `out[key] = f.get(str(index), 0)` 产出 health/max_health/level，**字段未下发时读作0**；而同函数 :312 已有 `stack_transmitted`、:314-316 已有 `unit_flags_transmitted` 的先例，说明该缺陷是遗漏而非设计。
4. 主控对379个存档trace、**17376个self采样**逐包复核：self的health字段（24）**从未下发**的采样有 **66个**，此时summary给出的 `health` 是0。档案 `results/pure_network/agent_wow_bridge_run40_quest788_kill1.packets.json` 即属此类：max_health=70已下发、health**从未下发**，而 `fight()` 的 `me['health']==0`（:33）会把它判成 **`self_dead`**。`agent_wow_finalize.py:19` 的 `health<=0` 和 `agent_wow_retreat.py:120-123` 的生命确认同样受影响。
5. `scripts/agent_wow_session_supervisor.py:7-30` 是当前恢复控制器 `settle()`：它在 `in_combat is True` 期间**再次调用 `fight()`**（:17），即恢复过程中**可能再次派发 `start_attack`**；`health_budget` 分支（:19-24）只是被动等待；`safe_to_logout`（:30）取**单次采样**的 `in_combat is False`。
6. `scripts/agent_wow_finalize.py:19-23` 同样以**单次采样** `in_combat is False` 作为登出依据。
7. `docs/34_攻击原始证据复核与结论纠正_2026-10-22.md:38` 已确认：**`AttackStop` 只停止本角色自动攻击，不会清除敌方仇恨或立即脱战；reason=1 是服务器报告仍在战斗**。因此脱战不能由AttackStop推断。
8. `docs/88_战斗撤离定位边界与只读契约验收.md` 已确认：**战斗中本机不广播自身移动**，`agent_wow_session.py:220` 恒为 `current_position_confirmed: False`。而 `fight()` 的距离用 `self_position`（`agent_wow_session.py:423-424` 取 current/observed/start 位置的其一，非战斗中确认位置）计算，因此**战斗中的距离不是已确认距离**。
9. `docs/76_长测试战斗方向审计与低血逃跑边界.md` 已确认：目标entry3101在本机 smart_scripts 中有 **0–15%生命时 Flee For Assist** 规则；目标5/69约7.25%与该区间相符，但**尚未逐包还原逃跑路径**，不能断言当次已完成逃跑。同文档确认玩家HP 108→95、目标HP 69→5（主控已独立复核存档包一致）。
10. `scripts/agent_wow_combat_control.py:6-23` 的 `estimated_position()` 返回的来源标签有多种：`last object update`、`server stop`、`unsupported motion; start only`、`linear receive-time estimate`、`linear endpoint estimate`。只有部分来源可用于距离判定。
11. 战斗控制器对外契约（**不得破坏**）：`fight()` 返回 `{'outcome','start_sent_once','observations','stop'}`；`wsl_task_runner.py:107` 依赖 `evidence['outcome']=='target_dead'`；`agent_wow_batch.py:72` 依赖同一字段；`audit_task_unit_evidence.py:69-70` 依赖 `evidence['phase']['outcome']` 与 `evidence['phase']['start_sent_once']`；`scripts/test_combat_distance_boundary.py` 断言 `target_left_melee`/`target_dead` 与 `bridge.stop_attack` 调用序列。

## 二、开始方式和隔离

**不要在主控工程写代码。**工作副本已由主控准备好：

```
工作副本：C:\Users\qrz\AppData\Local\Temp\opencode\pn-qwen-combat-state-001
分支：worker/combat-state-001
origin：指向同目录下的本地空裸库（scratch），push到任何地方都被禁止
```

在该目录下建立工作分支，**不要**再clone、不要新建目录。所有命令在该目录执行。输出仅落在该clone的 `.cache/combat-state-001/` 或下列指定交付路径，**不得读写主控工程的 `.cache`、不得读凭据**。
如平台权限拒绝，不变换命令绕过：记录被拒动作、继续独立可做部分，确实阻塞再找用户。

## 三、交付功能（一个整块完成，不逐项请示）

### A. 生命真值：区分"未下发"与"真为零"

- `scripts/agent_wow_worldstate.py` 的 `summarize()` 增加 `health_transmitted`（bool），语义与同函数已有的 `stack_transmitted`、`unit_flags_transmitted` 一致：**字段未下发时 health 不代表0**。
- `scripts/agent_wow_contract.py` 的 `Observation` 增加 `health_transmitted: bool = True`（默认True以免破坏既有构造），`scripts/agent_wow_session.py` 的 `observe()` 传入真实值。
- 状态机入口把未下发的health规范化为 `None`（未知），**未知不等于死亡、不等于低血**。
- 只做**加法**：不得改动既有键的取值、不得删除既有键、不得改变既有键的顺序语义。既有测试不得删改断言。

### B. 纯状态机（无副作用、可离线穷举）

新增 `scripts/agent_wow_combat_state_machine.py`：

- **纯逻辑**：不联网、不读文件、不发RPC、不用 `time.sleep`、不依赖真实时钟；时间与采样由调用方注入。所有决策输入必须是显式参数。
- 状态集合（**字符串必须精确使用下列名称**）：`precheck`、`engaging`、`in_combat`、`low_health_exit`、`target_out_of_range`、`disengaging`、`disengaged_confirmed`、`dead`、`ghost`、`unresolved`。
- 决策集合：`none`、`start_attack`、`continue_attack`、`stop_attack`、`request_escape`、`hold_and_observe`、`abort_unit`。
- 原因码集合（**字符串必须精确使用下列名称**）：`health_unknown`、`health_ratio_below_low`、`health_ratio_below_critical`、`self_dead`、`self_ghost`、`target_dead`、`target_missing`、`target_out_of_range_confirmed`、`target_distance_unconfirmed`、`out_of_combat_confirmed`、`out_of_combat_not_yet_confirmed`、`budget_exhausted`。
- 阈值配置化且**以max_health的比例表达**（如低血、危急血、确认次数、最短停留时长），禁止裸绝对血量比较；默认值需在报告中说明换算到 70/89/108 三种上限时的实际血量。
- **低血退出**：比例低于低血阈值 → 进入 `low_health_exit`，决策 `stop_attack`，并在有已规划撤离路径时给出 `request_escape`；**必须latch**：一旦低血退出，后续任何观测都不得回到 `in_combat`、不得再产生 `start_attack`。危急比例用独立原因码 `health_ratio_below_critical`。
- **目标离距**：必须区分"距离已确认超出阈值"与"距离不可确认"。前者 → `target_out_of_range` + `stop_attack` + 原因码 `target_out_of_range_confirmed`，**不得自动重启**；后者 → 仍须停止攻击，但原因码必须是 `target_distance_unconfirmed`，**禁止断言目标已逃走**（当前战斗中自身位置不确认，见事实8）。目标HP为0 → `target_dead`；目标不在视野 → `target_missing`；二者都不得记为离距。
- **脱战确认**：必须满足全部条件才进 `disengaged_confirmed`：(a) 连续 `out_of_combat_confirmations` 次采样 `in_combat` **已知为False**；(b) 期间最短停留时长达标；(c) 期间health**未下降**且已知>0；(d) health未知或 `in_combat` 未知（None）**必须重置计数**，且未知采样绝不计入确认。**单次采样不得确认**。状态机不得提供"没有采样即视为已脱战"的任何路径。
- **战斗期间禁止诊断链**：状态机不得进入任何采样/重放/诊断循环；预算耗尽即以 `budget_exhausted` 结束并给出 `unresolved` 或已规划的恢复决策。
- **死亡/幽灵**：`dead`、`ghost` 是独立终态，必须与"低血"区分；进入这两个状态后不得再产生攻击决策。

### C. 离线回放取证（真实证据）

新增 `scripts/replay_combat_state_machine.py`：用既有 `scripts/agent_wow_worldstate.py` 的 `IncrementalWorld` 逐包重建观察时间线，驱动状态机，输出 JSON 决策时间线（每步：序号、状态、决策、原因码、关键观测）。

- 必须**只读**存档，**不得写入或修改 `results/` 下任何文件**；输出路径由调用方指定。
- 必须覆盖并断言以下真实档案（主控已核验对应事实）：
  1. `agent_wow_bridge_quest792_long01_unit1_verify.packets.json`——真实战斗；HP 108→95、目标 69→5；必须产出单调决策时间线，且低血/退出之后**不得再出现 `start_attack`**。
  2. `agent_wow_bridge_run40_quest788_kill1.packets.json`——health字段从未下发；状态机**不得**报 `dead`（这是事实4的回归守卫）。
- 另需覆盖至少这些存档（存在性由你确认）：`agent_wow_bridge_run51_retreat_combat.packets.json`、`agent_wow_bridge_run113_confirmed_combat.packets.json`、`agent_wow_bridge_run116_final_tail_combat.packets.json`。
- 回放结论必须区分"已观察"与"未观察"，不得用采样缺失推断状态。

### D. 建议式接入（默认关闭、行为不变）

- `scripts/agent_wow_combat_control.py` 与 `scripts/agent_wow_session_supervisor.py` 可**咨询**状态机，并把其决策**记录进既有返回证据**（例如 `observations` 行新增状态/决策/原因码字段），但**不得**因此改变现有派发顺序、现有outcome字符串、现有 `start_sent_once` 语义。
- **本轮不启用强制执行**。强制接入（由状态机真正否决派发）是下一轮主控决定的事。你须在报告中列出"若强制接入，哪些现有分支会被改变"。
- 不得改 `scripts/wsl_task_runner.py`、`scripts/agent_wow_batch.py`、`scripts/audit_task_unit_evidence.py`、`scripts/agent_wow_retreat.py`、`scripts/agent_wow_effect_gate.py`、`scripts/agent_wow_task_boundary.py`、`scripts/agent_wow_finalize.py`、`scripts/agent_wow_session.py`（除 A 项的 `observe()` 传参外）。

## 四、允许范围与工具

主要阅读：`scripts/agent_wow_combat_control.py`、`scripts/agent_wow_session_supervisor.py`、`scripts/agent_wow_worldstate.py`、`scripts/agent_wow_contract.py`、`scripts/agent_wow_finalize.py`、`scripts/agent_wow_effect_gate.py`、`scripts/agent_wow_task_boundary.py`、`scripts/agent_wow_retreat.py`、`scripts/test_combat_distance_boundary.py`、`scripts/test_agent_wow_worldstate.py`、`scripts/test_finalize.py`、`scripts/test_agent_wow_contract.py`、`docs/34_*.md`、`docs/76_*.md`、`docs/88_*.md`。**不要扫描results大目录、不要读大量docs**；只读第三节点名的5个存档文件。

允许修改：

- `scripts/agent_wow_combat_state_machine.py`（新增）
- `scripts/replay_combat_state_machine.py`（新增）
- `scripts/test_combat_state_machine.py`（新增）
- `scripts/test_combat_state_machine_replay.py`（新增）
- `scripts/agent_wow_worldstate.py`（仅 A 项的加法）
- `scripts/agent_wow_contract.py`（仅 A 项的加法）
- `scripts/agent_wow_session.py`（仅 A 项的 `observe()` 传参）
- `scripts/agent_wow_combat_control.py`、`scripts/agent_wow_session_supervisor.py`（仅 D 项建议式接入）
- `docs/QWEN_COMBAT_STATE_001_交付报告.md`（新增）
- `.cache/combat-state-001/` 内运行日志与本地实验文件

如果发现必须改其他业务入口，先在报告中说明兼容方案，不顺手扩大改动。允许在自己的clone中运行Python离线测试、git status/diff/log/add/commit；**不允许push到任何远端、merge/rebase/reset/clean、修改Git配置或全局OpenCode配置**。禁止游戏/WSL登录游戏脚本、网络请求、服务器修改、凭据、父项目、旧证据删除/改写。

正常导入模块；**不改sys.path来掩盖导入问题**。使用下列unittest discover入口；辅助subprocess测试用 `sys.executable`。Windows PowerShell 5.1不使用`&&`。不吞错误或只截几行输出后声称全部成功。

## 五、工作方式与留痕

1. 开始保存基线commit、git status、计划和起始时间至 `.cache/combat-state-001/`。
2. 自主完成A–D，遇到失败先独立定位并修复；不每加两条测试就问用户是否继续。
3. 把实际执行命令、退出码、完整stdout/stderr、测试次数和修改理由留在日志。优先Python subprocess二进制捕获UTF-8，不用PowerShell重定向冒充UTF-8。
4. 如可取得OpenCode会话JSON事件就保留；如仅有命令日志，明确缺失工具级审计，不声称完整留痕。日志不含凭据，不把完整聊天提交公有Git。
5. 不以硬性"最多几次工具调用"限制完成整块工作；以**范围不越界、真实功能、测试和日志**验收。需要的正常代码修改可自主做，禁止行为不能因"必要"自行解禁。
6. 最终报告一次性提交；若核心任务确实阻塞，列出已验证原因、可交付部分和问题，不编造通过。

## 六、实际验收命令

```powershell
python -m unittest discover -s scripts -p "test_combat_state_machine*.py" -v
python -m unittest discover -s scripts -p "test_*.py"
python scripts/replay_combat_state_machine.py --help
git diff --check
git status --short
```

基线Windows中WSL专属用例会skip，必须报出跳过数量；**不要为了Windows全绿删测试**。本轮不要求你启动WSL实机环境；主控会补做WSL回归。**不能把单元测试通过当游戏战斗恢复成功**。

## 七、最终交付格式

在自己分支提交合格源码、测试和报告，不提交`.cache`；用户只需把**工作副本路径、branch、最终commit、报告路径**发给主控。不得自行推送。

报告必须包含：

1. 完成/未完成矩阵（A–D逐项），状态机状态图与关键转换。
2. 每个修改文件、兼容性说明、**不能证明的事项**。
3. 所有实际测试命令、退出码、tests/failures/errors/skipped；日志路径。
4. 回放取证证据：每个存档的决策时间线摘要，以及 `run40` 不得报dead、`long01` 不得在退出后再`start_attack` 的断言输出。
5. 阈值换算表：同一比例在 max_health=70/89/108 下的实际血量；与现有绝对阈值（25/50/60）的差异对照。
6. "若强制接入，哪些现有分支会被改变"清单。
7. 仍存在的限制、没有执行的实验；不得只写"全部通过"。
8. Git status、最终commit。若有任务外问题，仅列建议，不顺手改动。

**完成后停止，等用户通知主控验收。不要再自己派生新一轮任务。**
