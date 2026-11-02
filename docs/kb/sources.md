# 知识库 · 来源清单

> **准入规则**（`docs/72` 的"纳入知识库的规则" + AGENTS.md）：
> 每条来源必须标明**取得状态**，不得把"未取得正文"当"已读原文"。
> - `原文` = 通过原站实际取得正文
> - `镜像` = 通过公开镜像或本机副本取得
> - `摘要` = 仅有本地摘要/转述，**不得据此下结论**
> - `未取得` = 尚未取得正文

## A. 上游源码（**本机可查，最高价值**）

| 来源 | 本机位置 | 版本 | 取得 | 权威范围 |
|---|---|---|---|---|
| AzerothCore | `C:\Azerothcore\azerothcore-wotlk` | `eb3cdd1d1` | 原文 | **服务端机制的判定逻辑**：仇恨、leash、尸体回收、任务计数、寻路约束 |

**为什么它比任何网页都重要**：2026-11-01 的"角色反复死亡"根因，全部是服务端判定
（leash 半径 30 码、leash 计时器 11 秒、判定对象是**出生点**），
这些**在源码里是确定的常量与函数**，查一次就够，不用拿命试。
见 [[机制-脱战-leash]]。

## B. AgentWOW 原站（作者的项目）

| 来源 | 取得 | 权威范围 | 备注 |
|---|---|---|---|
| https://agent-wow.sh/gpt-6-astra-plays-world-of-warcraft-for-the-first-time-with-agent-wow/ | 原文 | 规划器结构（六个输入坐标、Recast/Detour、findNearestPoly 搜索范围 {5,12,5}、findStraightPath 用 DT_STRAIGHTPATH_ALL_CROSSINGS） | 已核验并复用：`nav.cpp` 采用该结构；作者自己报告过疑似穿墙，"optimal" **不是**无穿模的证明 |
| https://agent-wow.sh/five-agents-running-on-gpt-61-sol-clear-world-of-warcrafts-ragefire-chasm-in-24-hours/ | 原文 | 多层高度问题确实发生过；协作开发导航工具 | "水平接近 ≠ 可交互"；**不能**据此声称网页提供了完整高度差算法 |

## C. 外部教程（**只借鉴方法，不照搬参数**）

| 来源 | 取得 | 可借鉴 | 不可照搬 |
|---|---|---|---|
| https://drewkestell.us/Article/6/Chapter/20 | 原文 | 区分 Recast 生成可行走表面 与 Detour 查询路径；导航工具离线先验；用距离容差判断到点；动态目标需重规划 | 60 度坡度、1码/3码、25ms 等经验值（客户端/版本/控制方式不同）；"路径点数 >2 即成功"；客户端注入与 ClickToMove |

## D. 数据查询

| 来源 | 取得 | 用途 |
|---|---|---|
| https://wowgaming.altervista.org/aowow/?item=4919&xml 等 | 原文 | 物品数据（任务奖励核对） |
| https://goproxy.cn | 镜像 | Go 依赖代理 |
| https://reg.151077.xyz/ | 摘要 | 注册入口（**凭据类，一律不入库**） |
| https://docs.google.com/document/d/1mtYXJOZIVgk7kF1Lz0d-byX__swLpewuFYEJuz99K6w/edit | 未取得 | 纯网络路线对照（Google 不可达时以本地实验为准） |

## E. 本仓库

- 远端：`https://github.com/Wq5881898/wow-pure-network-research`
- 推送需代理：`git -c http.proxy=http://127.0.0.1:10808 push origin main`（**偶发失败，重试即可**）
- **网络不稳**：本机到 GitHub 的直连与代理都会间歇性失败。
  所以"把来源 URL 存下来"不如"**把本机镜像路径存下来**"有用。

## 相关

- [[index]] · [[机制-脱战-leash]] · [[教训-不要重做]]
