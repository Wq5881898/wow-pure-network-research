# 机制 · 脱战（leash）：怪什么时候会放弃

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

## 相关

- [[sources]] · [[index]] · [[教训-不要重做]]
