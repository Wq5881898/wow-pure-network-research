# 机制 · 脱战（leash）：怪什么时候会放弃

> ⚠️ **本文的"权威配方"已被 2026-11-01 的上游源码与实机数据推翻。**
> **真正的脱战机制是 [[机制-怪何时真正放弃-NO_PATH]]：到目标的路径不可达 → 怪放弃。**
> 下面保留 leash 相关事实，因为它们在"**能否重新锁定目标**"时仍然成立（风筝、再仇恨）。
> **不要再拿"离出生点 30 码 + 等 11 秒"当脱战方案。**

> **这是 2026-11-01 反复死亡的那个机制。** 查源码一次就够，不用拿角色去试。

## 判定（服务端源码，确定值）

| 事实 | 出处 | 值 |
|---|---|---|
| leash 半径 | `src/server/game/World/WorldConfig.cpp`：`CONFIG_CREATURE_LEASH_RADIUS`（配置名 `CreatureLeashRadius`） | **30.0 码** |
| leash 计时器 | `src/server/game/Entities/Creature/Creature.cpp`：`Creature::GetLeashTimer()` | **11 秒**（1–5 级怪；注释：低等级约 11s，高等级约 16s） |
| 判定对象 | 同上 `Creature::CanCreatureAttack()` | 比较 **`m_homePosition`（出生点）**，不是怪当前所在位置 |
| 伤害豁免 | 同上注释原文 | `don't check distance to home position if recently damaged (allow kiting away from spawnpoint!)` → **刚被打过的怪跳过 leash 判定** |
| 仇恨探测距离 | `Creature` 构造：`m_detectionDistance(20.0f)` | **20 码** |

## 权威配方（脱战）

1. **停止攻击** —— 否则 leash 计时器一直被续期（"允许带伤风筝离开出生点"）。
2. 走到**距怪的出生点 > 30 码**。
3. **距最后一次伤害 ≥ 11 秒** —— 之后怪重置，战斗解除。

## 我踩过的坑（都源于不知道上面三条）

| 我原来的做法 | 为什么错 |
|---|---|
| 撤离路线 = 离怪**当前**位置 45 码 | leash 看的是**出生点**；方向就错了 |
| 等待 = `12 秒 / 30 码`（40 码→16 秒） | 未必盖得住 **11 秒**计时器（且计时器从最后一次伤害算起） |
| 撤离到"离怪当前位置 45 码" | 怪会**跟着**走，出生点距离没拉开 |

## 已实装

- `scripts/agent_wow_exit_recovery.py`：`LEASH_RADIUS_YARDS = 30.0` / `LEASH_TIMER_SECONDS = 11.0`（带出处注释）
- 撤离路线**以怪的出生点为锚**（`movement.position_xyzo`，实测是静态可靠字段），
  终点放在离出生点 > 40 码（leash 30 + 余量 10）
- 撤离等待 = `走完路时间 + 11 秒 + 5 秒余量`
- 证据里记录 **实际落点** 与 leash 判定（`actual_end_distance_from_hostile_spawn`、
  `beyond_leash_radius`、`waited_past_leash_timer`），这样"撤离没解除战斗"能被拆成
  **距离不够** 还是 **时间不够**，不必再拿一次死亡换信息

## 仍未验证

- 实机读到一次完整的 leash 判定（需要一次"战斗未决 → 触发撤离"的运行）。

## ⚠️ 本条模型已被实机判定数据推翻（2026-11-01 b24_u1）

撤离证据里现在带 `leash_verdict`，b24_u1 实测：

```json
"actual_end_distance_from_hostile_spawn": 40.212,   ← > 30 ✓
"beyond_leash_radius": true,                          ← ✓
"waited_past_leash_timer": true,                     ← 等了 20.2s > 11s ✓
"retreat_outcome": "still_in_combat"                 ← ✗ 两个条件都满足，怪仍不放弃
```

**两个条件都满足了，怪却没有重置。** 所以上面的"权威配方"**不完整**。

**已知的问题**：我把 `CanCreatureAttack()` 里那套 leash 半径判定当成了"重置条件"，
但那段代码决定的是**能否再次锁定攻击目标**（`MoveInLineOfSight` 的选取路径），
**不是"放弃已有目标"的条件**。一旦怪已经有 victim，它会不会放手是**另一段逻辑**
（大概率在 `Creature::UpdateCreature` / `m_leashTimer` 的使用处）。

⇒ **下一步该做的**：在上游找到"怪何时真正放弃已有目标"的那段代码，
而不是继续用 `CanCreatureAttack` 的半径与计时器去推。

**在此之前不要把"撤离能解除战斗"当成已验证**：esc021 那次成功，是因为**目标恰好
没有跟上来**，不是因为满足了 leash 条件。

## 仍然有效的部分

- `Creature::GetLeashTimer()` = 11 秒（低等级怪）—— 这个值本身没错
- `CONFIG_CREATURE_LEASH_RADIUS` = 30 码 —— 存在且被用于"能否重新锁定目标"
- **"刚被打过的怪跳过 leash 判定"**（注释原文 allow kiting away from spawnpoint）—— 真实存在
- **脱战判定看出生点**（`m_homePosition`）—— 真实存在

## 相关

- [[sources]] · [[index]] · [[教训-不要重做]]
