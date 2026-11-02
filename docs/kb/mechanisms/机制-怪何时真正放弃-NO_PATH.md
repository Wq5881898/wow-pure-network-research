# 机制 · 怪何时真正放弃（NO_PATH）

> **这才是脱战的真正机制。** 2026-11-01 查上游源码得到，
> 并且**推翻了**我先前基于 leash 半径/计时器推出的模型（见 [[机制-脱战-leash]] 的更正）。

## 上游源码链条

| 环节 | 位置 | 内容 |
|---|---|---|
| 路径不可达 | `src/server/game/AI/UnitAI.cpp` | 怪寻路到目标失败 → `SetCannotReachTarget()` |
| 放弃 | 同上 | 只有一个目标 → **`ai->EnterEvadeMode(CreatureAI::EVADE_REASON_NO_PATH)`**；多个目标 → 清掉该目标的威胁 |
| 放弃动作 | `src/server/game/AI/CreatureAI.cpp` `_EnterEvadeMode()` | `CombatStop(true)` + `ClearLastLeashExtensionTimePtr()` + `SetCannotReachTarget()` → **战斗解除** |

**所以：怪放手的原因是"到目标的路径不存在了"，不是"跑远了"、也不是"过了多少秒"。**

## 为什么这解释了本项目的历史工作

`docs/80`–`docs/82` 做的正是**走廊边界撤离**：
把角色送到**怪无法寻路到达的网格边界**（另一个楼层、不可攀的落差、走廊边界），
让怪的目标变得"不可达" → 怪放弃。

既有产物（已验证，可直接复用）：
- `results/pure_network/height_R792_segment{1..6}_{plan,reconciled,trial}.json`
  —— 6 段走廊边界 staging 点，含独立核对与实机 trial 证据
- `scripts/agent_wow_retreat.py` —— `prepare()` 校验证据链后返回分段
- `scripts/plan_combat_retreat.py` / `survey_retreat_routes.py` /
  `trial_retreat_route.py` / `verify_retreat_boundary.py`
- 8 个 `test_retreat_*.py` / `test_agent_wow_retreat_controller.py`

**这就是本项目"撤离"的既定机制**，本轮应该复用它，而不是另造"跑远 40 码"。

## 对工程的影响

1. **撤离路线不是"离怪多远"，而是"怪还寻不寻得到你"**。
   ⇒ 规划撤离时必须验证：**从怪的位置到该点存在可达路径**（走得到 ⇒ 怪不会放弃）；
   要让怪放弃，就得走到**从怪的位置不可达**的点。
2. 导航工具的 `findPath` 在找不到路时报 `Incomplete path` / "surface walk left planned
   corridor" —— 这正是服务器侧"不可达"的对应物，可以直接用来判定。
3. **走廊边界不能凭 XY 距离认定**（docs/80 的结论）：要验证 visited 多边形的邻接、
   表面高度、是否可插入合法走廊；或用显式安全中间点重算分段路径，
   **保持完整连通与高度校验，不放宽规则**。

## 相关

- [[机制-脱战-leash]]（含被推翻的部分）· [[机制-位置与坐标的权威性]] · [[教训-不要重做]]
