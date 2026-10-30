# PN-AUDIT-011：独立克隆离线开发试运行

## 目标

给 Qwen 一次真正的生产代码＋测试小任务：改进 `scripts/audit_retreat_atomicity.py` 的**可行性判据表达**，让输出明确区分“桥接参数容量可装入单次请求”与“并未证明服务端到位”。

## 工作范围

- 独立本地克隆，不共享主仓库 Git 元数据；任务分支 `worker/pn-audit-011`。
- 允许读相关项目代码，修改 `scripts/audit_retreat_atomicity.py` 与新建/修改 `scripts/test_retreat_atomicity.py`；不得改其他源码、历史证据或文档。
- 不登录游戏、不移动角色、不使用网络、不 push/merge/rebase、不删除文件或清理 results。
- 可以运行指定的离线 unittest 命令，由主控复核完整输出。

## 具体变更

`audit(chunks)` 现在的 `one_call_full_route_possible` 表示容量和输入校验通过，名字容易被误读为已证实实际可行。新增 `one_call_parameters_fit` 布尔字段，与现有布尔值同值；保留旧字段向后兼容，但给报告增加明确的 `arrival_confirmed=False` 和 `scope='offline bridge input capacity only'`，不宣称真正服务端移动成功。测试至少覆盖合法路线与>50码的路径，验证两标记的差异与旧字段兼容。

## 验收

- 只改指定的生产文件与测试文件；不动 sys.path、凭据、历史数据或 Git 配置。
- `python -m unittest discover -s scripts -p test_retreat_atomicity.py -v` 通过。
- `python -m unittest discover -s scripts -p "test_*.py"` 通过。
- Qwen 最终汇报列出修改文件、命令结果、未做的事情和局限；主控复核 Git 差异、工具事件、测试结果。

## 留痕

命令执行期间的 OpenCode JSON 事件流与 stderr、启动配置（仅非敏感字段）、基线和结束 Git 状态，保存在本机忽略的 `.cache/worker_runs/PN-AUDIT-011/`。公开仓库只保存主控审核后的摘要。事件中可能包含路径、代码和模型响应，未经审查不上传。
