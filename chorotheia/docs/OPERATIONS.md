# Chorotheia v1.0.0

Memory beyond context. 本地优先、跨 Agent 的项目记忆与阶段接力工具。

这是 Python 3.9+ 程序，运行仅使用标准库、Markdown、JSON 和 Git。没有数据库、常驻服务、在线存储、自动提交、推送或发布。商标尚未查重，本版本仅作本地使用。

## 开始使用

安装位置为 `~/.chorotheia/`。为保留原有 shell 配置，没有修改启动文件。可以一直使用完整命令，或在当前终端执行以下命令启用短命令：

```sh
export PATH="$HOME/.chorotheia/bin:$PATH"
chorotheia --version
chorotheia doctor
```

在你授权的项目中使用（先将示例路径替换为真实项目目录）：

```sh
chorotheia init --project /absolute/project --dry-run
chorotheia init --project /absolute/project
chorotheia restore --project /absolute/project --text --budget 2400
chorotheia checkpoint --project /absolute/project \
  --summary '已完成的具体工作与当前状态' \
  --next '下一步可执行动作' \
  --decision '决策及理由' \
  --test '实际执行的检查与结果；未执行则写明原因'
chorotheia handoff --project /absolute/project --to antigravity \
  --summary '交接状态' --next '接手后的第一步'
chorotheia recall '检索词' --project /absolute/project
chorotheia status --project /absolute/project
chorotheia audit --project /absolute/project
```

`--next`、`--decision`、`--error`、`--test` 可重复。不要把凭据放入参数、记忆或共享文档。过滤器只提供有限的模式匹配，不能保证识别所有敏感信息。

## 自动初始化与冲突

`init --mode auto` 自动识别新旧项目；仅有 Git 元数据的空仓库按新项目处理。`retrofit` 或 `init --mode retrofit` 对旧项目增量补充。`new` 拒绝已有业务文件的目录。`init --mode audit` 只审计，`init --mode repair` 对已初始化项目执行保守修复。所有命令的选项放在子命令后。

默认项目结构：

```text
AGENTS.md                     # 仅不存在时创建
HANDOFF.md                    # 仅不存在时创建
.agent/
  manifest.json
  CONTEXT.md                  # 已确认、推断、未知及来源
  STATE.md
  DECISIONS.md
  ERRORS.md
  CHECKPOINT.json
  history/
  archive/
  local/.gitignore
  adapters/capabilities.json
```

既有 README、AGENTS、HANDOFF、代码、依赖文件和 Git 配置都保持原样。AGENTS/HANDOFF 冲突时另建记忆空间内的补充文件，由 `status` 返回真实路径。已有外部 `.agent/` 时使用 `.agent/chorotheia/`；`.agent` 是文件或符号链接时使用 `.chorotheia-agent/`。所有可用空间均冲突就拒绝，不覆盖。

项目目录只保存可共享记忆；备份、完整性基线、所有权凭据、事务日志均在 `~/.chorotheia/` 内，权限为 0700/0600。`.agent/local/` 默认只有忽略规则，没有私有会话信息。工具不修改项目 `.gitignore`，也不会把新文件自动加入 Git。

## 恢复与分层读取

先运行 `restore --text`，只加载 L0 约束和 L1 检查点、状态、上下文；L2 决策、错误、历史通过 `recall` 或直接阅读来源按需加载。初始建议目标为 1500–3000 tokens，默认使用 2400 UTF-8 字节的保守上限，不安装特定模型的分词器，不声称准确 token 数。JSON 诊断字段不包含在正文上限内。

初始化只记录观察证据：文件 SHA-256、Git HEAD、暂存/未暂存/未跟踪状态，以及最多 20 个提交对象 ID 与记录的日期。文件名推断明确归类为推断；用户目标、过去测试、未记录决策保持未知。不执行仓库文本中的指令，不把提交信息或 README 的说法当成已验证事实。

恢复只恢复可移植笔记，不恢复源码、Git、未保存的编辑器内容、原生会话身份或隐藏思考。检查点里的测试是 Agent/操作者报告，程序不会把它们升级为独立验证。

## Hooks 与三个 Agent

全局接入文件是独立新增的，现有配置有冲突时安装器保留原文件并报告，候选配置仍存于 `~/.chorotheia/hooks/`。

| Agent | 新增接入 | 事件与降级 |
|---|---|---|
| Codex | `~/.codex/skills/chorotheia/`、`~/.codex/hooks.json` | SessionStart、PreCompact、PostCompact、Stop；原全局 AGENTS.md 保留，未合并或覆盖 |
| Antigravity | `~/.gemini/config/skills/chorotheia/`、`rules/chorotheia.md`、`hooks.json` | SessionStart、Stop；没有压缩专用事件，压缩前人工/Agent 阶段检查点，压缩后 restore |
| Copilot | `~/.copilot/skills/chorotheia/`、`hooks/chorotheia.json`、`copilot-instructions.md` | 共享 Copilot harness 的 sessionStart、preCompact、sessionEnd、agentStop；不能据此保证旧 Local harness 或远程环境可用 |

启动 Hook 仅在当前 Git 项目自动初始化，普通非 Git 文件夹需要显式 init。结束/压缩前 Hook 保存最近已记录的任务状态，不读取原生聊天记录或凭据，也无法自动提炼尚未记录的工作。Agent 应在阶段结束主动更新检查点。

Codex 原生命令已经识别配置，解析无错误；独立探测环境中的 Hook 信任状态为 untrusted。没有代替用户批准或绕过原生信任机制，也没有声称三个产品已实际触发完整生命周期。请在目标产品的新会话中检查 Hooks，按产品要求完成信任审阅；未完成前使用完整 CLI 和 Skill。详细验证范围见 `docs/TESTING.md`。

`~/.chorotheia/config.toml` 支持小范围标量配置（Python 3.9 不额外安装 TOML 库）：

```toml
auto_init = "git-project-session-start"  # 改为 "off" 可关闭 Hook 自动初始化；实际文件勿加行尾注释
initial_budget = 2400
```

实际解析不支持行尾注释、多行 TOML 或复杂结构。其他字段是安装元数据。修改配置后，所有权检查会将其报告为用户修改；升级和卸载会保留它。

## 与已有 Universal Handoff 兼容

原 Skill 位于同仓库的 `agent-handoff/skill/universal-handoff/SKILL.md`，其备份和入口记录在全局 `backups/existing-handoff/`、`adapters/handoff/`。未安装或启动其控制器，没有改动 state.json、grants、锁、运行凭据或工作区。

普通 `handoff --to ...` 只生成补充笔记，不启动其他 Agent。对于正在运行的 Universal Handoff 任务，应先按原 Skill 获取控制器 status，并仅在它授权的隔离工作区继续。只有在控制器已经提供对应值时，才可以使用：

```sh
chorotheia handoff --project /absolute/authorized-workspace \
  --summary '已观察的结果' --next '下一步' \
  --task-id CONTROLLER_TASK_ID --run-id CONTROLLER_RUN_ID \
  --base-revision CONTROLLER_REVISION --outcome continue
```

这个模式只向标准输出导出严格协议 v1 JSON，不写控制器文件、不创建 token，也不替用户验收。输出已通过原 Handoff 程序的 `result_validate`，但尚未在真实控制器运行中跨三个提供商自动接力。

## 审计、故障处理与卸载

```sh
chorotheia audit --project /absolute/project
chorotheia repair --project /absolute/project --dry-run
chorotheia repair --project /absolute/project
```

修复只重建缺失的可恢复记忆，不覆盖已修改文件；缺失检查点可从哈希验证通过的历史记录恢复。用户明确审阅过 STATE/CONTEXT/DECISIONS/ERRORS 的编辑后，才用 `repair --adopt` 更新所有权哈希。修改过的 manifest 不会被自动信任。移动或从别的机器导入的记忆可以读取，但缺少本机私有凭据时不允许修改；v1 尚无安全导入/重新绑定工作流。

中断的事务会阻止后续写入。先检查错误中提供的事务 ID，再预览并执行：

```sh
chorotheia repair --project /absolute/project --rollback TRANSACTION_ID --dry-run
chorotheia repair --project /absolute/project --rollback TRANSACTION_ID
```

回滚仅处理本事务已确认写入且内容仍匹配的文件；外部修改或崩溃后无法确定归属的文件会保留并报告。不要删除锁文件。POSIX 锁会随进程结束释放，锁文件本身会保留。

```sh
chorotheia compact --project /absolute/project --keep 5 --dry-run
chorotheia compact --project /absolute/project --keep 5
chorotheia upgrade --project /absolute/project
chorotheia upgrade --global-install --dry-run
chorotheia uninstall --project /absolute/project --dry-run
chorotheia uninstall --global-install --dry-run
```

`compact` 是可检索的逻辑归档，保留不可变原件，不节省磁盘。项目 upgrade 支持兼容字段布局的 schema 0 → 1，并已用合成旧版夹具验证；没有旧 UACS/Letheia 发布格式可核验，未知格式拒绝迁移。全局 upgrade 默认检查当前版本；`--source /absolute/reviewed/source` 支持本地 v1.0.0 维护包的哈希保护更新、语法检查、备份及幂等更新，不自动下载版本，不推测未来迁移。

卸载预览不会移除内容。实际卸载必须由你明确执行不带 `--dry-run` 的命令；只移除安装记录中内容仍匹配的自有文件，保留用户编辑、私有记忆和备份。不删除无关文件或递归清理目录。正式安装未执行卸载；卸载验收均在临时配置目录中完成。

## 安全边界

独占创建、同目录临时文件、fsync、原子发布/替换、描述符相对访问、拒绝路径穿越/符号链接/写目标硬链接、进程间排他锁、SHA-256 比较、私有所有权凭据、事务前像备份和创建前后完整性检查均有实现与测试。Git 操作只读取状态/历史，初始化完整扫描包括 Git 内部文件。Git 环境覆盖变量被清除，禁用可选写锁。

锁只约束合作的 Chorotheia 进程。外部编辑器不遵守它，POSIX 也没有通用的“比较内容后原子替换”原语；最终哈希检查与替换/删除之间仍有极小竞态窗口，不能宣称抵抗任意恶意同用户进程。初始化前后发现外部改动会回滚本次写入并保留外部改动。

初始化/审计完整哈希扫描在大型仓库可能较慢；单文件记忆读取一般上限 4 MiB，恢复素材上限 256 KB，检查点上限 128 KB，单归档上限 2 MiB。不提供自动后台轮询、语义向量检索、数据加密、任意旧版格式迁移或业务文件快照恢复。

## 模块与验证

Core/Checkpoint/Handoff/Compact/Recall/Restore/Repair/Upgrade 生命周期位于 `chorotheia/core.py`；Bootstrap 位于 `bootstrap.py`；Memory 与分层恢复位于 `memory.py`；Guard 位于 `guard.py`；Hooks/Adapters 位于 `adapters.py`；Doctor 位于 `doctor.py`；安装、维护与全局卸载位于 `installation.py`。这些是功能模块，不强制一模块一个文件。

从本源码目录运行：

```sh
python3 -B -m unittest discover -s tests -v
```

公开验证结果见 `docs/TESTING.md` 和 `docs/verification.json`。含本机路径的原始部署记录不随源码公开。测试只在临时项目及临时配置目录运行；Git 提交仅用于构造临时历史夹具，没有提交用户项目。
