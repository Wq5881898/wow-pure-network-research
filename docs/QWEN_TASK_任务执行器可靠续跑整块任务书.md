# QWEN-RELIABILITY-001：任务执行器可靠续跑整块交付

**本文件是新窗口唯一必读交接，不需要读取历史聊天或逐篇阅读docs。**
角色：你是离线开发执行者，负责完成一个完整模块；主控窗口负责事后集中验收。用户已同意采用独立克隆开发，不共享主工作区。

## 一、目标和当前事实

本项目是AzerothCore纯网络路线，与客户端截图/Lua路线独立。

- 主控工程：`C:\APP\opencode\project\WoW自动化预研\pure-network`
- 远端：`https://github.com/Wq5881898/wow-pure-network-research`
- 交接基线：`7c243a0dc952e7707e5467e3d78bc71f7f851509`。
- 基线WSL离线全量165项通过；测试数字是基线记录，你须报告自己的实际结果。
- 本轮目标不是打怪或优化模型，而是将 **TaskExecutor 的检查点、动作计划绑定、并发保护、故障恢复** 做成可验收的可靠离线模块。
- 现状已有：请求前保存attempted；未确认动作不自动重发；无/坏检查点resume拒绝；已有检查点普通启动拒绝；临时文件flush/fsync后原子replace。
- 未闭合：同run同时首次启动的竞态；合法JSON但语义矛盾的检查点；同action ID换kind/params后续跑；检查点在执行器构造后发生变化；保存失败后的完整恢复证明。

## 二、开始方式和隔离

先确认当前目录。**不要直接在主控工程写代码。**新建独立clone（不是worktree，不共享.git）：

```powershell
# 以下父目录先Test-Path，已存在工作区不得覆盖或清理。
git clone --no-hardlinks --local "C:\APP\opencode\project\WoW自动化预研\pure-network" "C:\Users\qrz\AppData\Local\Temp\opencode\pn-qwen-reliability-001"
```

后续用该clone作为工具workdir，在其中建立`worker/reliability-001`分支。确认基线为上述提交或其包含本任务书的后续文档提交；基线不符先核对，不reset/强推。
所有命令默认在**自己的clone**执行。输出仅落clone中的`.cache/reliability-001/`或下列指定交付路径，不读主控.cache或凭据。
如平台权限拒绝，不变换命令绕过：记录被拒动作、继续独立可做部分，确实阻塞再找用户。

## 三、交付功能（一个整块完成，不逐项请示）

### A. 同一检查点的单执行者

- 在读取/校验检查点、决定派发之前获得检查点对应的执行锁；必须覆盖“两个执行器都先构造，随后竞争首次执行”的情况。
- 持锁期间才重新读正式检查点；不能信任构造时缓存的状态。普通run发现文件已存在即拒绝，不覆盖。
- 第二个执行者不得观察/派发后端动作、不得改写别人的检查点。不同检查点可独立运行。
- 锁包含owner标识和run ID。释放只能由本owner完成；不按时间长短自动删锁、不因未知PID就偷锁。
- 崩溃遗留锁可以保守阻塞并给出诊断，不要求本轮自动抢占。明确它是协作式锁，不是防恶意OS沙箱。
- 无checkpoint_path的纯内存模式保留兼容，但报告不具备跨进程持久化防重能力。

### B. 动作计划身份绑定

- 派发前验证action ID非空、不重复；kind、params、side_effecting及timeout应纳入持久化计划身份。
- 使用可复现的规范JSON和摘要，不用Python hash，不把函数repr/内存地址作为身份。
- 恢复时相同ID但kind/params/side_effecting/timeout或计划顺序变化应拒绝，不调用backend.execute。
- callable期望/前置条件不能可靠序列化；明确报告此限制，不伪造已绑定函数语义。不要通过字符串化函数来声称证明一致。
- 若升级schema：对旧schema采用明确迁移/拒绝策略，**不能把旧检查点当新运行清空重发**。保留原文，说明如何只读对账。合理更新既有测试以适配新字段，不删除旧失败断言。

### C. 检查点语义校验与恢复

- 拒绝非object顶层、重复动作标识、completed不属于计划、side-effecting的completed却无attempted、未知action ID，以及无法可靠解释的records/status矛盾。
- 仅有合法JSON和run ID一致不等于可信；校验失败返回明确未解决状态/原因，原文件字节保持不变，无后端派发。
- 保存前后中断保持旧或新**完整**JSON，不出现半份正式文件。已派发后保存失败，磁盘attempted必须足以让下一次显式resume不重发。
- 沿用当前UNKNOWN原则：后端异常不能证明没发送；明确`side_effect_applied=False`才可认定无效果。后置观测失败不能拿旧状态报成功。
- 没有显式expectation不能凭空跳过UNKNOWN动作；不要把任务失败等同于游戏脱战。

### D. 故障注入和离线集成验收

至少覆盖以下矩阵，测试要确实命中对应分支而不是提前因其他原因失败：

| 场景 | 期望 |
|---|---|
| 两个进程同时普通启动同checkpoint | 最多一个调用后端，其余拒绝；JSON完整 |
| 执行器构造后另一执行者先写checkpoint | 第二个run不覆盖/不派发 |
| 替换失败或fsync失败发生在派发前 | 零派发、旧字节保留 |
| 后端派发后最终保存失败 | 旧磁盘仍记录attempted，resume不重复派发 |
| 计划参数/种类/顺序变化 | 拒绝并保留文件 |
| 非法、缺失、矛盾checkpoint | 不重发、不覆盖 |
| 无变化正常计划 + 正常resume | 正常执行/只读验证已完成动作 |
| 超时、观察失败、UNKNOWN | 终止后续动作，续跑不重发 |

用FakeBackend和临时目录/可控时钟，不连接游戏。并发至少一项真实subprocess或multiprocessing测试；做好等待上限，不用长sleep碰运气。没有后端调用不能算“通过防重”——须同时证明获胜者确实执行一次。

## 四、允许范围与工具

主要阅读：`scripts/agent_wow_contract.py`、`scripts/test_agent_wow_contract.py`；必要时读`agent_wow_batch.py`、`wsl_task_runner.py`、现有测试了解调用兼容性。不要扫描results大目录或读取大量docs。

允许修改：

- `scripts/agent_wow_contract.py`
- 必要的新辅助模块：`scripts/agent_wow_checkpoint*.py`
- 对应现有/新增测试：`scripts/test_agent_wow_contract.py`、`scripts/test_checkpoint*.py`
- `docs/QWEN_RELIABILITY_001_交付报告.md`
- `.cache/reliability-001/`内运行日志/本地实验文件

如果发现必须改其他业务入口，先在报告中说明兼容方案，不顺手扩大改动。允许在自己clone中运行Python离线测试、git status/diff/log/add/commit；**不允许push、merge/rebase/reset/clean、修改Git配置或全局OpenCode配置**。禁止游戏/WSL登录游戏脚本、网络请求、服务器修改、凭据、父项目、旧证据删除/改写。

正常导入模块；不改sys.path来掩盖导入问题。使用下列unittest discover入口；辅助subprocess测试用sys.executable。Windows PowerShell5.1不使用`&&`。不吞错误或只截几行输出后声称全部成功。

## 五、工作方式与留痕

1. 开始保存基线commit、git status、计划和起始时间至`.cache/reliability-001/`。
2. 自主完成A–D，遇到失败先独立定位并修复；不每加两条测试就问用户是否继续。
3. 把实际执行命令、退出码、完整stdout/stderr、测试次数和修改理由留在日志。优先Python subprocess二进制捕获UTF-8，不用PowerShell重定向冒充UTF-8。
4. 如可取得OpenCode会话JSON事件就保留；如仅有命令日志，明确缺失工具级审计，不声称完整留痕。日志不含凭据，不把完整聊天提交公有Git。
5. 不以硬性“最多几次工具调用”限制完成整块工作；以**范围不越界、真实功能、测试和日志**验收。需要的正常代码修改可自主做，禁止行为不能因“必要”自行解禁。
6. 最终报告一次性提交；若核心任务确实阻塞，列出已验证原因、可交付部分和问题，不编造通过。

## 六、实际验收命令

```powershell
python -m unittest discover -s scripts -p "test_agent_wow_contract.py" -v
python -m unittest discover -s scripts -p "test_checkpoint*.py" -v
python -m unittest discover -s scripts -p "test_*.py"
git diff --check
git status --short
```

基线Windows中WSL专属用例会skip，必须报出跳过数量；**不要为了Windows全绿删测试**。本轮不要求你启动WSL实机环境；主控会补做WSL回归。不能把单元测试通过当游戏续跑成功。

## 七、最终交付格式

在自己分支提交合格源码、测试和报告，不提交`.cache`；用户只需把**clone路径、branch、最终commit、报告路径**发给主控。不得自行推送。

报告必须包含：

1. 完成/未完成矩阵（A–D逐项），方案和关键状态转换。
2. 每个修改文件、兼容性/旧schema策略、不能证明的事项。
3. 所有实际测试命令、退出码、tests/failures/errors/skipped；日志路径。
4. 故障注入证据，尤其“两进程一个真实派发”和“派发后保存失败不重发”。
5. 仍存在的竞态/崩溃限制、没有执行的实验；不得只写“全部通过”。
6. Git status、最终commit。若有任务外问题，仅列建议，不顺手改动。

**完成后停止，等用户通知主控验收。不要再自己派生新一轮任务。**
