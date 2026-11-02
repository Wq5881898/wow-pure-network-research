# 机制 · 怪何时真正放弃（完整模型）

> **这是脱战的权威判定。** 2026-11-01 查上游源码 + 实机判定数据得到。
> 取代我先前基于 leash 半径推出的模型（见 [[机制-脱战-leash]] 的更正）。

## 入口：怪为什么会放手

`Creature::CanCreatureAttack(victim)` 返回 false 时，
`ThreatReference::ShouldBeOffline()`
（`src/server/game/Combat/ThreatManager.cpp`）会把该受害者的威胁引用下线：

```cpp
bool ThreatReference::ShouldBeOffline() const
{
    if (!_owner->CanSeeOrDetect(_victim)) return true;
    if (!_owner->_IsTargetAcceptable(_victim) || !_owner->CanCreatureAttack(_victim)) return true;
    ...
}
```

⇒ **怪丢失目标 = 战斗解除。** 所以问题变成：**`CanCreatureAttack` 会在什么情况下返回 false？**

## `CanCreatureAttack` 的全部否决条件

源码：`src/server/game/Entities/Creature/Creature.cpp`

| # | 条件 | 参照点 | 受"刚被打过"豁免影响吗 |
|---|---|---|---|
| **A** | `!victim->isInAccessiblePlaceFor(this)` —— **受害者在怪到不了的地方** | 导航 | ❌ **最先检查，完全不受豁免影响** |
| **B** | `!IsWithinDist(victim, visibility)` —— **超出可见距离** | **怪的当前所在** | ❌ **不受豁免影响** |
| **C** | 距**出生点/重置位** > 30 码（`IsInDist2d`，**2D**） | `m_homePosition` 或 idle motion 的 reset position | ✅ **豁免期间跳过** |
| D | `victim->IsInMap` / `IsValidAttackTarget` / `isInAccessiblePlaceFor` / `IsInEvadeMode` / 5 秒宽限期 / FD | — | — |

**关键顺序**：`visibility` 与"刚被打过"的豁免判断在**同一段**里，但豁免是**提前 return true**：

```cpp
if (!GetCharmerOrOwnerGUID().IsPlayer())
{
    if (GetMap()->IsDungeon()) return true;
    float visibility = std::max<float>(GetVisibilityRange(), victim->GetVisibilityRange());
    if (!IsWithinDist(victim, visibility)) return false;      // 条件 B：无条件生效
    // "don't check distance to home position if recently damaged"
    if (!isWorldBoss() && (GetLastLeashExtensionTime() + GetLeashTimer() > now || HasTauntAura()))
        return true;                                          // 豁免：跳过条件 C
}
...
float dist = sWorld->getFloatConfig(CONFIG_CREATURE_LEASH_RADIUS);   // 30
MovementGenerator* idleSlot = GetMotionMaster()->GetMotionSlot(MOTION_SLOT_IDLE);
if (idleSlot && idleSlot->GetResetPosition(x, y, z)) return IsInDist2d(x, y, dist);
else return IsInDist2d(&m_homePosition, dist);                          // 条件 C
```

## 三个条件的实际含义

### A · 不可达（NO_PATH）—— 唯一在"怪还在打你"时也生效的脱离方式

路径到不了 ⇒ `isInAccessiblePlaceFor` 为假 ⇒ 威胁引用下线 ⇒ 怪放弃 ⇒ 脱战。
上游对应 `EVADE_REASON_NO_PATH`
（`src/server/game/AI/UnitAI.cpp` → `CreatureAI::_EnterEvadeMode()`）。

**实测（2026-11-01，b24 战斗区）**：
以角色所在位置 (-198.85, -4325.22, 66.63) 为圆心，**200 码内**共 24 个角色可达的地面点，
**全部 24 个怪也能寻路到达**；已验证的"走廊边界终点" (-352.01, -4303.79, 53.27)
从怪的位置寻路**同样 PASS**（13 个多边形）。
⇒ **该区域不存在天然的分离点**，靠"走到怪到不了的地方"是走不掉的。

### B · 超出可见距离 —— 参考点是**怪的当前所在**

```cpp
float visibility = std::max<float>(GetVisibilityRange(), victim->GetVisibilityRange());
```

- 玩家（受害者）的可见距离 = 地图的可见距离 =
  `DEFAULT_VISIBILITY_DISTANCE` = **100 码**（大陆）
  （`src/server/game/Entities/Object/ObjectDefines.h`）
- 怪的可见距离 = `detection_range` × 视野系数（更小）

⇒ `max` 取 **100 码** ⇒ 条件 B = **离怪的当前所在 3D 距离 > 100 码**。

**这是"跑远"真正要跑出的距离。**
参照点是**怪现在站的地方**，不是它的出生点 —— 怪会追上来，所以按出生点算会严重低估。

**跑出这个距离要多久？（用实测速度推导）**

| 量 | 值 | 出处 |
|---|---|---|
| 角色奔跑速度 | **7 码/秒** | 桥接 `run=True` |
| 怪的追击速度 | `speed_walk` × 2.5 = **2.5 码/秒** | 本机 world DB：entry 3101 / 3124 的 `speed_walk = 1.0`（`speed_run = 0.857`，`detection_range = 10`） |
| 间距增长速度 | **4.5 码/秒** | 差值 |
| 需要的跑动时间 | (100 − 8) / 4.5 = **20.4 秒** | 起点按近战 8 码算 |
| 需要的路径长度 | 7 × 20.4 ≈ **143 码** | |
| **实装取值** | `ESCAPE_MIN_DISTANCE_YARDS = 165`（离出生点） | 路径实测 167 码、末间距约 **115 码**（余量 15 码） |

**代价**：跑动约 **24 秒** ≈ 12 次攻击 ≈ 掉 60–85 血
⇒ **角色 HP 低于约 85 时，逃跑注定致死**，此时应改选别的结局
（`ESCAPE_MIN_HEALTH = 85`）。

**离线验证工具（跑实机之前必须先跑，零代价）**：
`python scripts/plan_escape_route_offline.py <角色x> <角色y> <角色z> <出生点x> <出生点y> <出生点z> [reach]`
→ 输出每个方向的路径长度、跑动秒数、需要的桥接请求数。

### C · leash 半径 —— 实战中几乎不可能触发

30 码、2D、参照出生点，且**只在"最近 11 秒没被伤害"时生效**。
但 leash 扩展时间会被反复刷新：

| 刷新点 | 含义 |
|---|---|
| `Creature.cpp` `JustEngagedWith` | 接战时 |
| `Unit.cpp` `SetInCombatWith` → "Update leash timer when attacking creatures" | 每次开打 |
| `Unit.cpp` `DealDamage`（非 DOT、受害者是怪） | **每次受到伤害** |
| `TargetedMovementGenerator.cpp` | **怪在近战范围内时，每个攻击 tick 都刷新** |
| `Creature.cpp` 定时器 | 失控（LOST_CONTROL）时每 3 秒 |

上游注释原文：
> `// Mobs should chase you infinitely if you stop and wait every few seconds.`

⇒ **怪还在打你的时候，条件 C 永远不生效。** 这就是 b24_u1 失败的原因：

- 角色停手 → 怪继续追、继续打 → leash 扩展每个攻击 tick 刷新 → 豁免一直有效；
- 角色只走了 29 码 → 怪跟得上 → 始终在近战/可见距离内 → 条件 A、B 都不成立；
- ⇒ `retreat_outcome = still_in_combat`。

**b24 的判定数据（两个 leash 条件都满足却仍失败）**：

```json
"actual_end_distance_from_hostile_spawn": 40.212,   ← > 30 ✓
"beyond_leash_radius": true,                          ← ✓
"waited_past_leash_timer": true,                     ← 等了 20.2s > 11s ✓
"retreat_outcome": "still_in_combat"                 ← ✗ 怪不放手
```

## 由此带来的**脱战择优规则**（已实装）

撤离不是免费的：跑完 24 秒 ≈ 挨 12 次 ≈ 掉 78% 的生命预算。
⇒ **低于 `ESCAPE_MIN_HEALTH_RATIO = 0.78` 时，撤离是"送死跑"**，
此时若目标**已近死且够得着**，应**先打死目标**（一锤的事，不用跑）。

改在**唯一那条**规则里（`agent_wow_combat_state_machine.choose_combat_ending`），
状态机与退出分支共用：

| 条件 | 结局 |
|---|---|
| 没有可用撤离路线 | `fight`（不许空等） |
| 生命值 ≤ 健康底线 | `escape`（维持既有契约：太残就别开新战） |
| 目标近死 + 够得着 + 角色 ≥ 50% | `finish_target`（既有契约） |
| 角色 ≥ 78%（能撑住这趟跑） | `escape` |
| **角色 < 78% + 目标近死 + 够得着** | **`finish_target`**（新增：跑会死，一锤能结束就别跑） |
| 其余（跑会死 + 目标不近死） | `escape`（宁可死在离怪远的地方，尸体更好跑） |

**注意**：这条**改了项目方一条既有契约**（原"低血时一律撤离"）。
理由是机制模型的修正（撤离从"29 码"变成"165 码 / 24 秒"），
已同步更新 `scripts/test_combat_ending_rule.py` 的断言与说明。

## ⇒ 实用的脱离配方

1. **停止攻击**（否则怪每 tick 刷新 leash 豁免，且你会继续挨打）。
2. **跑到离怪的当前所在 > 100 码**（条件 B）。
   - 桥接 `run=True` 是 **7 码/秒** ⇒ 100 码约需 **15 秒**；
   - 桥接单次请求上限 50 码、每段 ≤15 码 ⇒ **必须分段下发**；
   - 参照点用**怪的实时位置**（世界状态里的 `server_motion`），不是出生点。
3. 或者走到**怪寻路到不了**的点（条件 A）—— 实测本战斗区 200 码内不存在这样的点。
4. **兜底**：条件都不成立时，打死目标或**死掉跑尸**（死亡会清空战斗状态）。
   项目已有的跑尸链路（`agent_wow_danger_recovery.py`，dr03 实机通过）是可用的兜底。

## 已实装的代码位置

- `scripts/agent_wow_exit_recovery.py`
  - `VISIBILITY_RANGE_YARDS = 100.0`、`ESCAPE_MIN_DISTANCE_YARDS = 165.0`、`ESCAPE_MIN_HEALTH = 85`
  - `_plan_escape_route(backend, min_distance=ESCAPE_MIN_DISTANCE_YARDS)`
  - 判定记录：`visibility_range_yards`、`escape_min_distance_yards`、
    `distance_from_hostile_spawn`、`beyond_visibility_from_spawn`
    （旧的 leash 字段保留为诊断）
- `scripts/plan_no_path_retreat.py` —— 条件 A 的离线搜索器
  （`find_separation_point`：找"角色可达 ∧ 怪不可达"的点），配
  `scripts/test_plan_no_path_retreat.py`（5 项单元测试）。

## 相关

- [[机制-脱战-leash]]（含被推翻的部分）· [[机制-位置与坐标的权威性]] ·
  [[机制-桥接路径限制与分段]] · [[教训-不要重做]]
