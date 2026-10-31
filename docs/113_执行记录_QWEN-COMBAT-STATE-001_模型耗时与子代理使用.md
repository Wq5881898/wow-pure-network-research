# 执行记录：QWEN-COMBAT-STATE-001 战斗恢复状态机整块交付

本文件记录**一轮**完整工作的执行模型、执行内容、耗时、工具调用与子 agent 使用情况，作为后续每一轮的可比对模板。

数据来源：OpenCode 本地会话库 `~/.local/share/opencode/opencode.db`（只读查询）、`.cache/worker_runs/QWEN-COMBAT-STATE-001/` 原始事件日志（UTF-8 原始字节）、`git log`。所有数字均来自机器记录，非人工估算。

## 一、这一轮是什么

| 项 | 值 |
|---|---|
| 任务 | QWEN-COMBAT-STATE-001：战斗恢复状态机（低血退出、目标离距、脱战确认）整块交付 |
| 主控会话 | `ses_ea9ddc410ffe4nADSGBwXO8t4G`（目录 `C:/APP/opencode/project/WoW自动化预研`） |
| 主控执行模型 | `minimax/MiniMax-M3.1-Flash`（本轮全程 233 条 assistant 消息，**无其他模型介入**） |
| 工作副本 | `C:\Users\qrz\AppData\Local\Temp\opencode\pn-qwen-combat-state-001`（独立 clone，origin 指向本地裸库） |
| worker 分支 | `worker/combat-state-001` |
| worker 执行模型 | `qwen/ARC4NUM/Qwen3.8-Flash-Next-Uncensored-MLX-Serve-4bit` |
| worker 会话 | `ses_e6ee17444ffePzNM88oL8a9Fmg` |
| 合入提交 | `429e5a8` → `510a3e0` → `9bdd679` → `33c1286`（主仓库 `main`） |

## 二、时间线（主控）

| 时间 | 事件 |
|---|---|
| 10-31 15:58:16 | 本轮主控开始（模型切换到 minimax/MiniMax-M3.1-Flash） |
| 10-31 16:11:44 | 用户指令：下发"战斗恢复状态机"整块任务书 |
| 10-31 16:37 | 任务书提交 `0793a0c`（`docs/QWEN_TASK_战斗恢复状态机整块任务书.md`），已推送 |
| 10-31 16:39–17:55 | worker 10 轮下发，3 轮修正 |
| 10-31 18:03 | 合入并验收提交 `33c1286`，已推送 |
| 11-01 03:16:38 | 续做：本记录文档 + 实机验证准备 |

**主控净活跃时长 129.8 分钟**（2.2 小时）。分段：10-31 15:58→18:03 共 125.7 分钟，11-01 03:16→03:20 共 4.0 分钟；两段之间是窗口关闭的空闲期，未计入。

## 三、worker 执行明细（10 轮）

事件日志：`pure-network/.cache/worker_runs/QWEN-COMBAT-STATE-001/roundN/events.jsonl`（原始 UTF-8 字节，未经过 PowerShell 重定向）。每轮由 `scripts/capture_worker_run.py` 以子进程原始文件描述符捕获。

| 轮 | 墙钟 | 事件数 | 工具调用 | 结果 |
|---|---:|---:|---:|---|
| smoke | 21.7s | 18 | 5 | 权限配置冒烟测试（故意写越界文件，确认被拒） |
| round1 | 581.0s | 238 | 80 | 首轮整块交付（**600s 预算耗尽被中断**） |
| round2 | 549.8s | 341 | 110 | 续做：回放脚本与回放测试（**预算耗尽**） |
| round3 | 579.8s | 181 | 60 | 续做：报告与提交（**预算耗尽**） |
| round4 | 512.3s | 172 | 56 | 主控第 1 轮修正下发（4 个问题） |
| round5 | 124.0s | 40 | 13 | 续做：报告与提交；**末段遇 MLX 显存 503** |
| round6 | 0.0s | 1 | 0 | **MLX 503：显存预算耗尽，未产生任何工具调用** |
| round7 | 0.0s | 1 | 0 | 同上（续接同一 session 重试） |
| round8 | 0.0s | 1 | 0 | 同上（改用全新 session + 自包含提示词） |
| round9 | 24.7s | 20 | 6 | 显存恢复，worker 开始正常产出 |
| round10 | 433.8s | 175 | 55 | 完成第 2 轮修正、报告同步与提交 |

worker 累计墙钟 **2827.2 秒（47.1 分钟）**，累计工具调用 **374 次**。

三轮 503 的性质：MLX 推理服务返回 `503 Not enough memory to load model`，属**基础设施错误**，不是 worker 行为错误。round6–8 三轮 0 工具调用即因此产生——日志中只有一条 error 事件，无任何 `tool_use`。处理方式：等待服务恢复后原 session 续跑（round9/round10 成功）。

## 四、工具调用统计

### 主控（本轮）
| 工具 | 次数 |
|---|---:|
| bash | 164 |
| read | 56 |
| write | 33 |
| grep | 23 |
| edit | 6 |
| todowrite | 3 |
| glob | 2 |
| webfetch | 1 |
| **task（子 agent）** | **0** |
| **合计** | **288** |

失败调用：`read` 2 次（路径不存在）；`invalid` 3 次（模型尝试调用当前环境不存在的 `apply_patch` 工具，被运行时拒绝——见第七节）。

### worker（10 轮合计）
| 工具 | 次数 |
|---|---:|
| bash | 233 |
| edit | 109 |
| read | 18 |
| write | 7 |
| todowrite | 4 |
| grep | 3 |
| **task（子 agent）** | **0** |
| **合计** | **374** |

## 五、子 agent 使用情况

**本轮主控与 worker 都没有调用任何子 agent（`task` 工具调用数为 0）。**

全部工作由主控会话与单个 worker 会话直接完成，产物经独立克隆隔离、主控验收后合入。

补充事实：本项目历史上主控会话总共只调用过 **1 次** 子 agent，是 10-29 05:38 的 `explore` 类型（"检查任务运行时缺口"），**不属本轮**。worker 的权限配置中 `task` 被显式设为 `deny`，因此 worker 在结构上不可能派生子 agent。

## 六、这一轮实际做了什么（按动作）

### 6.1 主控：先取证，再写任务书
写任务书之前没有凭记忆下笔，而是先在真实存档上核实事实，结果直接成为任务书的硬要求：

- **发现现存缺陷 A**：对 379 个存档 trace、17376 个 self 采样逐包重放，确认 `agent_wow_worldstate.summarize()` 用 `f.get(str(index), 0)` 产出 health，**字段未下发时读作 0**；其中 66 个采样 health 从未下发，`agent_wow_bridge_run40_quest788_kill1.packets.json` 会被 `fight()` 误判 `self_dead`。
- **发现现存缺陷 B**：血量阈值全是绝对值（25/50/60），而本机 max_health 随等级为 70/89/108，同一个 25 在三档下分别是 35.7%/28.1%/23.1%。
- **核实既有边界**：`docs/34`（AttackStop ≠ 清除仇恨或立即脱战）、`docs/88`（战斗中不广播自身位置 → 距离不可确认）、`docs/76`（目标 0–15% 血量会 Flee For Assist）。

### 6.2 主控：下发与限权
建立独立 clone，origin 改指本地裸库（push 物理上无效），编写进程级权限配置（`share: disabled`，逐工具逐路径 allow/deny），先跑 **smoke 轮**验证限权生效——故意让 worker 写越界文件，确认 2 次调用被拒（`read`/`write` 各 1 次拒绝），并由此**发现 bash+python 可绕过 edit 白名单**这一固有局限，据此把验收重心放在"合入前逐文件审计 diff"而非依赖运行时权限。

### 6.3 主控：独立对抗验收（不采信 worker 自报数字）
worker 首轮交付后，主控**自写探针**（而非复用 worker 的测试）复验，发现 4 个问题并下发第 1 轮修正：

1. 脱战确认窗口被污染后最短停留不重新计时（0.2 秒干净段即可确认脱战）；
2. 直接构造 `ObservationView(health=0, health_transmitted=False)` 仍被判定核心判为 `dead`；
3. `start_attack` 在穷举约 2000 条观测路径下**一次都不可达**，导致"低血后不重启"的断言是空断言；
4. 报告措辞与实现不符。

第 1 轮修正引入新缺陷（`_advance()` 每次重置窗口起点 → dwell 恒为 0 → 脱战永远无法确认），**worker 自己的新测试如实报出 4 个 FAIL**。第 2 轮修正后转绿。

### 6.4 主控：最终验收与合入
- 独立复跑：Windows 全量 **283 通过 / skipped=11**（基线 225 + 新增 58）；WSL 全量 **283 通过、无跳过**。
- 独立复核 `health_transmitted`：379 个存档逐样本核对 summary 与原始字段 24，**零不一致**。
- 独立规格探针 S1–S7 全部成立；低血 latch 后连续 5 次采样**无一次** `start_attack`；`start_attack` 确实可达（穷举可复现），故 latch 断言**非空转**；`in_combat` 未知时**不授权** `start_attack`。
- 变更面审计：`git diff --name-only 0793a0c -- scripts/` 仅含本任务文件，**基线 225 项既有测试断言零删改**；`results/` 零改动；`.cache` 未入库。
- 主控侧再做一处修正：`test_precheck_unknown_combat_advises_start_attack` 改名（该用例现在的断言是"未知战斗状态**不得**建议 start_attack"，原名与断言相反会误导读者），**未改动任何断言逻辑**。
- 全部提交推送，`33c1286` 云端与本地一致。

### 6.5 worker：实际产出
3 个提交：`42459ce`（整块交付，10 文件 +1638 行）→ `56492dd`（第 2 轮修正，3 文件）→ `48bda5c`（报告同步，1 文件）。新增 `scripts/agent_wow_combat_state_machine.py`（纯状态机）、`scripts/replay_combat_state_machine.py`（离线回放 CLI）、`scripts/test_combat_state_machine.py`、`scripts/test_combat_state_machine_replay.py`；修改 `agent_wow_worldstate.py`（新增 `health_transmitted`）、`agent_wow_contract.py`、`agent_wow_session.py`、`agent_wow_combat_control.py`、`agent_wow_session_supervisor.py`（建议式接入，**默认关闭**）。

## 七、需要如实记录的问题与代价

- **3 次 MLX 503**：worker 模型服务显存预算耗尽，round6–8 三轮零产出。已按可重试错误恢复，但**确实浪费了 3 轮预算**。若改为"每轮缩短提示词 + 更小预算"或提前检测显存水位，可减少空转。
- **3 次 `apply_patch` 不可用**：模型仍按旧习惯调用当前环境不存在的 `apply_patch`，被运行时拒绝。改用 `edit`/`write` 后正常。属可自愈的工具名幻觉。
- **bash 可绕过 edit 白名单**：进程级权限不是操作系统沙箱，`bash` + `python` 可写任意路径。已通过"独立 clone + origin 指向裸库 + 合入前逐文件审计 diff"兜底，但这是**权限模型的固有缺口**，不是本轮新引入。
- **worker 自报数字不可直接采信**：本轮 worker 首轮自报"全量 271 通过"，主控独立复跑确实为 271；但第二轮的 4 个 FAIL 说明**其自测基线本身可能漏覆盖**。本轮的实际价值来自主控独立探针，不是 worker 的自检。
- **强制接入未验证**：状态机目前只是默认关闭的建议式接入，**真正否决派发的那一层从未实机跑过**。283 项离线测试不等于战斗恢复在游戏中成功。
- **本轮零实机**：最短停留 2.0 秒、确认次数 3、max_range 5.0 码均未实机标定。

## 八、并行窗口说明

同一时间 `ses_e7e8cf077ffea2HNZ2b1lKLoLW`（父项目目录）由**用户另行开窗**操作，模型 `qwen/ARC4NUM/...`，内容为 MLX 503 排查与"换模型重试"。该窗口与本轮**不是**父子关系（`parent_id` 均为 `None`），也未向本轮注入任何信息；本轮记录的 MLX 503 与用户在该窗口的排查相互印证。

## 九、下一步（记录基准）

1. 实机受控单元：验证状态机在真实采样下的表现，重点是 `in_combat` 未知与脱战确认路径。
2. 强制接入决策：基于实机结果决定是否开启，并单独验收。
3. 后续每一轮继续沿用本模板记录：执行模型（精确到 provider/model）、起止时间与净活跃时长、工具调用明细、子 agent 调用数、worker 分轮墙钟与工具数、以及**如实记录的问题与浪费**。
