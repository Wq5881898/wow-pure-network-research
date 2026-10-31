# QWEN-RELIABILITY-001 交付报告

- 克隆路径：`C:\Users\qrz\AppData\Local\Temp\opencode\pn-qwen-reliability-001`
- 分支：`worker/reliability-001`
- 基线：`83332172b8f6563408d63b5dd328f74e231211e4`（任务书文档提交，包含任务书声明的基线 `7c243a0`，`git merge-base --is-ancestor` 验证通过）
- 开始时间：2026-10-31 14:30（本地）
- 平台：Windows / Python 3.13.2（`C:\APP\Python\python.exe`）

## 1. 完成/未完成矩阵（A–D）

| 项 | 状态 | 实现位置 |
|---|---|---|
| A. 同一检查点的单执行者 | 完成 | `scripts/agent_wow_checkpoint.py` 的 `CheckpointLock`；`TaskExecutor.run` 在读检查点/决定派发前取锁，持锁期间重读正式检查点（`_load_checkpoint`），不再信任构造时缓存（原 `_checkpoint_present` 构造时快照已删除）；锁文件含 owner/run/pid，释放仅认 owner，不按年龄/PID 偷锁；崩溃遗留锁保守阻塞并输出 JSON 诊断；无 checkpoint_path 的纯内存模式保留兼容并明确无跨进程防重（`test_in_memory_mode_has_no_persistence`） |
| B. 动作计划身份绑定 | 完成 | `plan_identity()`：对 id/kind/params/side_effecting/timeout 按规划顺序做规范 JSON（sort_keys、紧凑分隔符、禁 NaN/Infinity）后取 SHA-256；派发前校验 id 非空、不重复；恢复时身份不符即拒绝且不调用 `backend.execute`（`test_resume_with_changed_*` 四类）；schema 2 检查点持久化 `plan_digest` 与 `plan` 条目 |
| C. 检查点语义校验与恢复 | 完成 | `validate_checkpoint()`：拒绝非 object 顶层、run_id 不符、重复动作标识、completed/attempted/records 引用计划外 ID、completed 无成功记录、成功记录不在 completed、side-effecting completed 缺 attempted、未知记录状态；拒绝时原文件字节不变、零派发；schema 1 采用明确拒绝策略（不当新运行清空重发），只读对账用 `scripts/agent_wow_checkpoint_reconcile.py` |
| D. 故障注入与离线集成验收 | 完成 | `scripts/test_checkpoint_recovery.py`（矩阵全项）、`scripts/test_checkpoint_concurrency.py`（真实 subprocess 双进程）、`scripts/test_checkpoint_identity.py`（身份/校验/锁单元） |

关键状态转换：
1. `run()` → 计划校验（不取锁、不触后端）→ 取锁 → 持锁重读+校验检查点 → 普通启动遇已存在文件（合法或非法）一律 UNKNOWN 拒绝、不写盘不派发；resume 遇缺失/不可信一律 UNKNOWN 拒绝。
2. 副作用派发前：`attempted` 追加后立即原子落盘（首次派发前还先落一份运行存在性检查点，防止后来者双首发）；派发后每次结果都原子落盘。
3. resume 中 `attempted` 未确认的副作用：只观察不重发；确认则记 `skipped_verified` 并把验证事实持久化（`success_verified` 记录，不伪造 `success` 执行记录）；未确认则 UNKNOWN 终止。
4. 新增收紧：resume 中 `completed` 动作的后置条件不再成立时，不再静默重发（旧行为会重派可能带副作用的动作），改为 UNKNOWN 终止要求对账（`test_resume_rejects_invalid_checkpoint_without_overwrite_or_dispatch` 的旧断言 `calls==[]` 原样保留并因此继续通过）。

## 2. 修改文件与兼容性

| 文件 | 变更 |
|---|---|
| `scripts/agent_wow_contract.py` | 重写执行器骨架：加锁、持锁重读、schema 2 写入、pre-dispatch 存在性落盘、`success_verified` 持久化、completed 后置条件破坏时停止；UNKNOWN/`side_effect_applied=False`/观察失败原则原样保留 |
| `scripts/agent_wow_checkpoint.py` | 新增：规范 JSON/SHA-256 计划摘要、`validate_checkpoint`、`read_checkpoint`、`CheckpointLock` |
| `scripts/agent_wow_checkpoint_reconcile.py` | 新增：只读对账 CLI（不写、不锁、不派发；无 `--plan` 时只报 `identity_unverified`，不冒充身份已验证） |
| `scripts/test_agent_wow_contract.py` | 既有 21 项全部保留且通过；仅 4 项的**手写 fixture** 从 schema 1 升到 schema 2（断言未删）；另在 flush/replace 故障两项后**追加**"磁盘证据阻止重发"断言。`git diff` 可核对（基线测试 sha256 `dd4ef6d8…`，当前 `cbb72f54…`） |
| `scripts/test_checkpoint_identity.py` | 新增 29 项：摘要复现性/字段绑定/键序无关/坏计划拒绝；全部语义拒绝分支；锁生命周期、不偷锁、陈旧锁保守阻塞、畸形锁阻塞 |
| `scripts/test_checkpoint_recovery.py` | 新增 30 项：故障注入矩阵（见下） |
| `scripts/test_checkpoint_concurrency.py` + `test_checkpoint_lock_worker.py` | 新增 2 项真实 subprocess 测试 |

兼容性说明：
- `wsl_task_runner.py` 未改动：构造签名与 `run(actions, resume)` 返回值结构不变；其运行报告为一次性文件（run ID 复用即拒绝），新增强化（completed 后置条件破坏→UNKNOWN、pre-dispatch 存在性落盘）只会更保守。
- `agent_wow_batch.classify` 已核对：`success_verified`/`skipped_verified` 记录不会进入其必需动作集合判定路径（它只认 `success` 执行行与 `confirmed_by` 证据），分类语义不变。
- 旧 schema 1 检查点：执行器一律拒绝并给出 legacy 提示；原字节保留；用 reconcile 工具只读对账。

不能证明的事项（未伪造）：
- `precondition`/`expectation` 是 callable，无法可靠序列化，**不在计划身份内**；恢复无法证明两个 callable 未变（字符串化 repr 不算证明）。params 含非 JSON 类型（set/bytes/自定义对象）时身份无法建立，直接拒绝。
- 锁是协作式的：不防恶意 OS 对端，不做进程存活探测，不做自动抢占。
- 检查点存在本身证明"上一执行者曾持有锁并写过盘"，不证明其后端动作在游戏侧的实际效果。

## 3. 实际验收命令与结果（Windows，本 clone）

| 命令 | 退出码 | 结果 | 日志 |
|---|---|---|---|
| `python -m unittest discover -s scripts -p "test_agent_wow_contract.py" -v` | 0 | Ran 21, OK | `.cache/reliability-001/acc_1_contract.txt` |
| `python -m unittest discover -s scripts -p "test_checkpoint*.py" -v` | 0 | Ran 60, OK | `.cache/reliability-001/acc_2_checkpoint.txt` |
| `python -m unittest discover -s scripts -p "test_*.py" -v` | 0 | **Ran 225, failures=0, errors=0, skipped=11**, OK | `.cache/reliability-001/acc_3_all.txt` |
| `git diff --check` | 0 | 仅一条 LF→CRLF autocrlf 提示，无空白错误 | — |
| `git status --short` | 0 | 仅本任务 2 改 6 新，见第 6 节 | — |

跳过 11 项全部为 `backend bootstrap requires WSL`（与基线 Windows 行为一致，未删任何测试）。基线全量数（WSL 165）未在本机复现——本轮未启动 WSL，留给主控回归。

## 4. 故障注入证据

矩阵覆盖（`test_checkpoint_recovery.py`）：
- 两进程同时普通启动同 checkpoint：`test_two_processes_one_dispatch`（真实 subprocess，构造屏障+门文件释放，无 sleep 碰运气）。
- 执行器构造后另一执行者先写 checkpoint：`test_construction_before_peer_write_still_refuses_to_overwrite`。
- 派发前 fsync/replace 失败：`test_fsync/replace_fault_before_dispatch_*` + `test_pre_dispatch_durable_write_fault_propagates_and_keeps_bytes`（零派发、旧字节/无半文件、临时文件已清理）。
- 后端派发后最终保存失败：`test_final_save_fault_after_dispatch_keeps_attempted_and_resume_no_resend`。
- 计划 kind/params/side/timeout/顺序变化：4+1 项，全部拒绝且原字节不变、零派发。
- 非法/缺失/矛盾 checkpoint：顶层非 object、截断 JSON、重复 attempted、completed 无记录、legacy schema、成功记录与 completed 矛盾，共 6 项，均不重发不覆盖。
- 正常计划+正常 resume：`test_unchanged_plan_runs_and_resume_only_verifies`、`test_late_postcondition_on_resume_confirms_without_resend`。
- 超时/观察失败/UNKNOWN：`test_timeout_then_resume_never_resends`、`test_observe_failure_never_reports_success_and_resume_holds`。

"两进程一个真实派发"实证（`.cache/reliability-001/demo/evidence.json`，由 `.cache/reliability-001/run_race_demo.py` 生成，可复跑）：
- 两进程均先完成执行器构造（ready 屏障各写 1 字节）后同时放行；
- 结果：仅 1 个 dispatch 标记文件（O_EXCL 创建，等价后端真实调用一次）；落败者输出含 `"reason": "another executor holds the checkpoint lock"` 的诊断 JSON；
- 正式检查点为完整 schema 2 JSON（`attempted:["a"]`），结束后锁文件不存在。
- 稳定性：修复断言后 `test_checkpoint_concurrency` 连续 35 次全过；首轮全量曾暴露一次断言过紧（胜者状态可为 success 或 unknown），已修正判定而非放宽核心不变量（恰好一次派发+恰好一次锁拒绝始终断言）。

"派发后保存失败不重发"实证：dispatch 一次后最终保存注入 replace 失败 → 磁盘仍为 attempted-only → 再次显式 resume 零 `backend.execute`，确认则 `success_verified/skipped_verified`、不确认则 UNKNOWN "refusing to resend"。

## 5. 仍存在的竞态/崩溃限制与未执行实验

- **崩溃窗口（保守阻塞）**：持锁进程在"锁文件创建成功、payload 写入前"崩溃 → 遗留空/半锁文件，后续执行者保守阻塞（`test_malformed_stale_lock_still_blocks`），需人工确认后删锁；本轮按任务书不做自动抢占。
- **释放竞态（极窄）**：release 的"重读确认 owner→unlink"非原子；若本 owner 的锁在他方 unlink 与他方 create 之间被第三方抢先重建，理论上可能误删他人新锁。派发防重仍由"持锁重读检查点+原子写"兜底。未实现依赖 `ReplaceFile`/`LockFileEx` 的加固（见第 6 节建议）。
- **锁与检查点是两次操作**：崩溃可能留下"锁在、检查点未写"或反之；语义均为保守拒绝，无自动修复。
- **callable 期望/前置条件**不参与身份（见第 2 节）。
- **未执行**：WSL 实机回归、真实游戏/后端续跑、任何网络与服务器操作；单元测试通过≠游戏续跑成功。
- 本环境无工具级会话 JSON 事件可导出，留痕为命令日志+退出码+完整输出文件（`.cache/reliability-001/`），不含凭据，不声称完整工具级审计。

## 6. Git status 与最终提交

提交前 `git status --short`（`.cache/` 被 .gitignore 排除，未提交）：

```
 M scripts/agent_wow_contract.py
 M scripts/test_agent_wow_contract.py
?? scripts/agent_wow_checkpoint.py
?? scripts/agent_wow_checkpoint_reconcile.py
?? scripts/test_checkpoint_concurrency.py
?? scripts/test_checkpoint_identity.py
?? scripts/test_checkpoint_lock_worker.py
?? scripts/test_checkpoint_recovery.py
```

最终提交 ID：见本分支 HEAD（提交信息 `reliability-001: ...`），未 push、未 merge/rebase/reset/clean。

任务外观察（仅建议，未改动）：
1. `scripts/agent_wow_session.py` 的 `PureNetworkBackend` 未实现 `Action.precondition` 相关约束，与本任务无关。
2. 若未来要防"崩溃锁自动回收"，可在 Windows 用 `msvcrt.locking`/Linux `flock` 在锁 fd 上加 OS 建议锁做存活探测；本轮保持协作式语义。
3. `wsl_task_runner.write_json_atomic` 与检查点原子写逻辑重复，可日后收敛到公共模块。
