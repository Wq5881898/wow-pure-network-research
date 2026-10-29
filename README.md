# WoW 纯网络交互预研

AzerothCore 本地实验的独立工程，使用协议会话、JSON-RPC 事件桥和离线解码，不依赖截图或客户端输入。

## 入口

- `docs/00_上下文交接.md`：目标、实验状态、约束与后续任务。
- `docs/66_删除事故直接证据与快照恢复_2026-10-29.md`：事故直接证据。
- `scripts/agent_wow_batch.py`：有界批次协调器。
- `scripts/wsl_task_runner.py`：单任务及跨会话核对。
- `probes/AgentWowBridge/`、`probes/AgentWowSDK/`、`probes/AgentWowNav/`：协议与导航工程。
- `results/pure_network/`：当前证据目录，保留内层目录以兼容现有脚本。

## 离线检查

```text
python -m unittest discover -s scripts -p "test_*.py"
```

真实运行前需准备 WoWLab、AzerothCore 与忽略目录内凭据。当前批次恢复/锁逻辑仍待修复，禁止把离线测试通过当作真实任务验收。

## 来源

从父项目纯网络路线拆分，逐文件原路径及 SHA-256 见 `project_migration.json`。AGENTS.md 初始复制自父项目治理说明；截图与插件路线仍在父项目。历史报告可能含旧路径，读取时以本工程根目录为准。

公有仓库不保存原始聊天、账号凭据或未经检查的认证流量；它不是这些私有资料的云端备份。
