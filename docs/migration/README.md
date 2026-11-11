# 并列独立工程迁移（2026-11-11）

## 目录与完整性

- 来源：`C:\APP\opencode\project\WoW自动化预研\pure-network`
- 当前：`C:\APP\opencode\project\wow pure network`
- WSL：`/mnt/c/APP/opencode/project/wow pure network`
- 沿用 https://github.com/Wq5881898/wow-pure-network-research ，分支 `main`。
- 复制前 HEAD：`94c2d96a8c768361401ce973c358e102f9c32ac7`。保留 `.git`、本地分支、reflog、未提交改动和忽略材料。
- 初始复制 29,004 文件、1,166,765,255 字节。复制前源哈希、复制后源哈希及目标哈希逐项一致；见 `copy-verification.json`、`source-file-manifest.json`。
- 原目录、父客户端工程及已运行的客户端会话保留。迁移期间没有登录游戏或继续练级。

## 父工程文档来源

`parent-document-manifest.json` 保存相对来源、归档路径、SHA-256 和逐文件选择理由。
归档原件为父 `AGENTS.md`、拆分前交接快照、文档审阅任务分工、客户端危险区恢复对照及客户端发布恢复审核，共5份。
归档内容仅作来源证据，内含客户端约定与历史状态，不作为当前执行指令。
根目录与 docs 直接目录共104份 Markdown 有取舍记录；同号客户端文档未替换纯网络文档。

## 路径修改

- 五个旧 WSL 启动器不再硬编码原嵌套目录，通过 `project_paths.wsl_path(ROOT)` 推导当前工程路径；参数数组保留含空格路径为单一参数。
- `leveling_run.py` 的 shell 工作目录转换为 WSL 路径并用 `shlex.quote` 引用。
- VSS 审计明确固定到删除事故发生时的父工程来源路径，不能随新工程目录改变历史查询位置。
- `wsl_lab_connect.py` 将网关发现延迟到实际 relay 使用时，允许 Windows 离线导入；运行入口仍记录实际网关。
- 历史 results、来源清单、恢复副本中的路径不改写；历史任务检查点不是可以直接继续派发的实时任务状态。
- `build-cache-inventory.json` 记录4个既有 CMake cache。保留原始构建缓存作为迁移材料；需要重建时使用新的 `.cache` 构建目录，不能直接复用旧 cache 的源/构建目录。

## 本地依赖与公开边界

- `.cache/` 全部保留本地，包括凭据、认证流量、私有会话、事故恢复原件、worker材料和构建产物；不提交。
- 原始 results 共11,210文件完整保留。新增 results 默认忽略，逐文件审核后才能显式暂存；历史已跟踪证据继续保留版本历史。本次不批量上传6,600余项未跟踪实验原始产物。
- 超过100MiB的本地材料是 `.cache/incident_private` 中279,421,652字节私有会话，保留本地且禁止公开；未强行上传或删除。
- 公共源码/文档候选逐文件记录大小、SHA-256、凭据模式扫描结果，私有审查清单位于 `.cache/migration-2026-11-11/public-review.json`。自动扫描不能替代人工语义审阅。
- 仍依赖本机 AzerothCore 源码、数据库配置、mmaps，以及 WoWLab 中 `~/wow-research` 的 CLI/桥接工具链。当前 WoWLab 未运行，未自动启动。
- `run_contact_validation.py` 引用本机临时 local-realm-cli 和 corridor nav；`leveling_run.py` 引用临时恢复/观测脚本。它们属于后续实机前须重新核实的外部依赖，本次未执行。
- `.git/worktrees` 保留三个原临时 worker 工作树的登记。它们的外部 `.git` 回指原工程，不运行 repair/prune，也不在新工程使用这些旧 worker；避免修改原工程依赖。

## Git 对象核验发现

复制来的 pack `pack-d222d51343e2d203b812d50e24d789a73279f5ae` 原本存在坏对象头/解压错误；复制哈希一致，错误不是本次复制引入。
原 pack 和 idx 已移至本工程忽略目录 `.cache/migration-2026-11-11/corrupt-pack-original/` 留存，原工程不动。
隔离后 `git rev-list --objects --all --missing=print` 没有缺失，所有分支可达历史完整。
`git fsck --full` 仍报告某个 reflog 可达旧树引用4个缺失 blob（其中2个原已损坏、另外2个仅位于隔离包）；不删除 reflog 或伪造对象。
这些对象 GitHub API 查询无对应公开对象，本地同名 results 亦不匹配原 Git 哈希，无法宣称已恢复。
GitHub 大流量 mirror clone 超时，两次 refetch 连接重置；小请求 API 与 ls-remote 成功。这是实际连接结果，不归因为仓库权限。
损坏原件、完整 Git 状态/差异快照和修复尝试日志均在本地迁移缓存。

## 本次离线验证结果

- 最终 Windows 全量 `python -m unittest discover -s scripts -p "test_*.py"` 等价发现执行：499项，0错误、0失败、18跳过；其中481项实际通过。
- 203份 Python 源文件 AST 语法检查通过。
- 迁移后再次核对全部11,210份原始 results，SHA-256 无变化。
- 全部分支可达对象缺失数为0；reflog损坏对象仍按上节保留为遗留问题。
- `git diff --check` 通过；新增补丁文件的上下文前缀+Go制表符由 `.gitattributes` 声明为补丁格式，不改写原始补丁。未做 WSL/Go/C++重新构建、游戏登录或真实整链实验；原有实验代码按历史工作快照提交，不宣称全部实机验证。
- 早期运行曾遇到Windows导入ip route失败（已修复）及一次并发屏障用例偶发失败（单项复测和最终全量通过）；失败输出在当前OpenCode工具记录中，最终详细测试日志保留 `.cache/migration-2026-11-11/offline-tests-final.txt`。

## 实验交接状态

重点读 `docs/185`、`188`、`190`、`191`。历史最后确认 Netprobe/GUID9 为8级、2108/5400XP；dw撤回Huklah后203满血、非战斗、满耐久、204铜并正常登出，不代表实时状态。
后续仍需修复接敌/撤离后恢复主任务、跑步有界反馈、相对安全路径记录；主动6级与5级自卫计数分离。
迁移和离线测试不能代表所有历史未提交实验代码或真实整链验证通过；击杀、脱战、回落、登出分别验收。
