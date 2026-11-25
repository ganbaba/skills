# Chorotheia v1.0.0

**Memory beyond context.** 本地优先、跨 Agent 的项目记忆、阶段检查点和交接工具。Python 3.9+ 标准库运行，无数据库、守护进程或默认网络存储。

- [Chorotheia Skill](skills/chorotheia/SKILL.md)
- [完整操作说明、安全边界与故障恢复](docs/OPERATIONS.md)
- [逐项测试与验收限制](docs/TESTING.md)
- [已有 Universal Handoff](../agent-handoff/README.md)

## 安装

```sh
git clone https://github.com/ganbaba/skills.git
cd skills/chorotheia
python3 -B -m unittest discover -s tests -v
python3 -B install.py --dry-run
python3 -B install.py \
  --handoff-skill ../agent-handoff/skill/universal-handoff/SKILL.md
~/.chorotheia/bin/chorotheia --version
~/.chorotheia/bin/chorotheia doctor
```

安装目标默认为 `~/.chorotheia/`，自动备份相关原配置，仅独占新增自己的文件。已有 Hooks、Skill 或规则冲突时保留原文件并报告，不覆盖或合并未知设置。不修改 shell 启动文件、不安装其他 CLI、不启动模型服务，不代替用户批准原生 Hook 信任。`--no-register` 仅安装程序，不注册 Agent 接入文件。

仅需指引时可以单独阅读 Skill；Skill 调用 CLI，完整功能仍需安装程序。

## 使用

```sh
export PATH="$HOME/.chorotheia/bin:$PATH"  # 仅当前终端生效
chorotheia init --project /absolute/project --dry-run
chorotheia init --project /absolute/project
chorotheia restore --project /absolute/project --text --budget 2400
chorotheia checkpoint --project /absolute/project \
  --summary '已观察的状态' --next '下一步' --test '实际命令和结果，或未执行原因'
chorotheia handoff --project /absolute/project --to antigravity \
  --summary '交接状态' --next '接手后的第一步'
chorotheia recall '检索词' --project /absolute/project
chorotheia audit --project /absolute/project
```

支持 `init`、`retrofit`、`checkpoint`、`restore`、`handoff`、`recall`、`compact`、`status`、`audit`、`repair`、`doctor`、`upgrade`、`uninstall`。初始化支持 auto/new/retrofit/repair/audit 模式与 dry-run。

项目使用 AGENTS.md、HANDOFF.md 和 `.agent/` 下的 Markdown/JSON、history、archive、local、adapters。已有 README、代码、依赖和文档保持原样；路径冲突则使用独立补充空间并报告。私有备份、基线、事务及所有权凭据位于全局 0700 目录，不写入项目共享记忆。

证据按“已确认 / 推断 / 未知”区分，记录来源、SHA-256 与 Git 状态。初始只读取 L0/L1，其余按需检索；建议初始目标为 1500–3000 tokens，默认正文采用保守 2400 UTF-8 字节上限，不声称精确分词计数。

## Agent 接入与 Handoff

Codex 提供 SessionStart、PreCompact、PostCompact、Stop 配置；Antigravity 提供 SessionStart/Stop，并通过全局规则和 Skill 降级处理压缩；Copilot 提供共享 Copilot harness 配置，不承诺旧 Local harness 或远程环境兼容。

Git 项目可在可信启动 Hook 中自动初始化，非 Git 目录需显式 init。Hook 只保存最近已写下的状态，无法读取未记录思考。原生 Hooks 的真实会话自动触发尚未全链路验收；Codex 配置已通过本机原生发现/解析，但仍需用户的原生信任审阅。安装配置不等于自动触发成功。

Chorotheia 为同仓库 Universal Handoff 提供补充笔记和协议 v1 导出。原控制器仍管理任务、独立工作区、授权与运行凭据。协议导出必须使用控制器提供的 task_id、run_id、base_revision；不会启动另一 Agent、伪造 token 或修改原控制面。真实跨服务自动接力尚未联调。

## 安全与当前限制

实现独占创建、原子写入、fsync、文件锁、SHA-256 比较、初始化前后完整性验证、描述符相对路径防护、私有所有权凭据和事务回滚。默认不 commit/push/reset/clean/stash，不恢复或覆盖业务源码。卸载仅移除仍与自有记录匹配的文件，保留修改、私有记忆与备份。

合作文件锁不能约束外部编辑器；最终校验与替换/删除之间仍有极小竞态窗口。敏感信息过滤是启发式，不保证识别全部秘密。恢复不包含隐藏思考、原生会话或未保存文件；compact 为保留原件的逻辑归档，不节省磁盘。未知旧版 UACS/Letheia 格式拒绝猜测迁移；没有本机所有权凭据的导入记忆暂为只读。

品牌采用 Chorotheia，商标查重尚未完成。本次提交源码不代表注册商标、发布到包注册中心或完成所有原生服务验收。
