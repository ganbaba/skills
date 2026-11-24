# 五种 Agent 的实际适配

核实日期：2026-11-24，Asia/Hong_Kong。这里区分“官方支持”“本机探测”和“真实服务联调”。所有真实调用均需任务级授权；本次没有向任何模型发送项目内容，没有核查账户或用量。

| 工具 | 官方非交互入口 | 本包输出适配 | 本机验证 | 不可自动运行时 |
|---|---|---|---|---|
| Codex CLI | `codex exec` | JSONL 最终 agent_message + turn.completed | `0.171.0-alpha.13`，所用参数出现在本机 help | 人工 Prompt 接力 |
| Antigravity CLI | `agy -p` | JSON envelope 的 status/response | 未找到 `agy` | IDE/App 人工接力，不使用 `antigravity` 启动器冒充 headless CLI |
| GitHub Copilot CLI | `copilot -p` | `-s` 纯文本里的最终协议 JSON | 未找到 `copilot` | IDE Chat 人工接力；不使用旧 `gh copilot suggest` 冒充 coding agent |
| Claude Code | `claude -p` | result/success envelope | 未找到 `claude` | Claude Code 交互会话人工接力 |
| Cursor Agent CLI | `agent -p` | result/success envelope | 未找到 `agent` | Cursor 编辑器人工接力；`cursor` 打开文件不等于运行 Agent |

上表的 CLI 接口分别见 [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[Antigravity headless](https://antigravity.google/docs/cli/headless/)、[Copilot 程序化运行](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically)、[Claude Code headless](https://code.claude.com/docs/en/headless)、[Cursor headless](https://cursor.com/docs/cli/headless)。

## 本包固定参数

具体 argv 的单一实现来源是 `handoff/adapters.py`，不会从仓库或交接文本执行任意命令模板。

| 工具 | 参数策略 | 自动执行限制 |
|---|---|---|
| Codex | exec / json / workspace-write / ephemeral / ignore-user-config / skip-git-repo-check | 允许副本内工作；不持久化项目 trust，不自动批准额外权限 |
| Antigravity | print / output-format=json / mode=default / sandbox | 需要交互确认的动作可能被 soft-deny；返回 0 也需有效交接结果 |
| Copilot | prompt / silent / no-ask-user / no-auto-update / allow-tool=read,write | 默认只新增文件读写许可；没有授予 shell、URL、all-paths 或 trust-folder |
| Claude | print / bare / json / dontAsk / permission-prompts=none / tools 和 allowedTools 仅 Read,Edit,Write,Glob,Grep / no-session-persistence | 不开放 Bash；缺少测试或权限时应阻塞；bare 不等于隔离全部设置 |
| Cursor | print / output-format=json | 不加 force；受用户/团队权限配置影响，默认 print 可能编辑工作区文件 |

权限与输出细节来源：[Codex 官方说明](https://learn.chatgpt.com/docs/non-interactive-mode)、[Antigravity 权限与状态](https://antigravity.google/docs/cli/headless/)、[Copilot 程序化参考](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)、[Claude CLI 参数](https://code.claude.com/docs/en/cli-reference)、[Cursor 输出格式](https://cursor.com/docs/cli/reference/output-format)。这些权限并不等价；详见 SAFETY.md。

`doctor` 只调用本机 help，核对需要的参数。不会登录、发送模型请求、控制 GUI 或读取配额。`authorize` 保存可执行文件绝对路径和 SHA-256；每轮运行前检查哈希，CLI 更新后停止，需重新审查并授权。对于脚本启动器，只能固定启动器本身，不能固定其动态依赖或外部模型；功能探测也不验证程序发行者身份。请检查 doctor 所显示的路径。

help 缺参数、CLI 缺失或显式加入 `config.json` 的 `manual_agents` 时进入人工模式。本包不根据一个程序名称猜测别名：例如旧安装只有 `cursor-agent` 而没有 `agent`，就使用人工模式，待依据对应版本文档更新适配并测试。

CLI 已安装但登录过期、团队禁用 headless、授权不够或协议输出不正确时，会停止并给出固定错误码；控制器不解析错误文案来猜原因。修复账户/权限后可人工续作，或先 pause/revoke，再将该工具加入 `manual_agents`，授权后 resume。没有自动登录、自动改设置或自动接受工具权限。

## Skill 的交互式安装位置

本包的自动 CLI 调用直接在 Prompt 中提供协议，不依赖各厂商自动发现 Skill。Claude 的 bare 模式会跳过 Skill 发现。下面是人工作业时可选的个人目录；复制完整 `universal-handoff` 文件夹，不只复制 SKILL.md，不写现有项目目录。

| 工具 | 当前文档列出的个人 Skill 位置 | 交互式使用 |
|---|---|---|
| Codex | `~/.agents/skills/universal-handoff/`；旧 `~/.codex/skills/` 仍须按客户端核实 | `$universal-handoff` 或按语义选择 |
| Antigravity | `~/.gemini/config/skills/universal-handoff/` | `/universal-handoff`；CLI 可 `/skills reload` |
| Copilot CLI | `~/.copilot/skills/universal-handoff/` 或 `~/.agents/skills/` | `/skills reload` 后 `/skills info universal-handoff` |
| Claude Code | `~/.claude/skills/universal-handoff/` | `/universal-handoff` |
| Cursor | `~/.cursor/skills/universal-handoff/` 或 `~/.agents/skills/` | 检查当前客户端的 Skills 列表 |

路径依据：[Codex Build skills](https://learn.chatgpt.com/docs/build-skills)、[Antigravity Skill 发现](https://antigravity.google/docs/customization-discovery?tab=cli)、[Copilot 添加 Skill](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)、[Claude Skills](https://code.claude.com/docs/en/skills)、[Cursor Skills](https://cursor.com/docs/skills)。Cursor 的个人 Skill 云端同步是独立设置，本包不启用它；本地安装不代表授权上传。[Cursor 同步说明](https://cursor.com/docs/skills)

## 配额与会话边界

本版不提供通用“剩余额度”接口，不抓取 GUI、cookie、钥匙串或私有接口。部分工具有 usage 展示或服务专用 SDK，但没有据此实现跨厂商、跨计划的稳定额度查询。所有探测结果都把 login/quota 记为 unknown。

有效 `continue` 会沿用户授权路线轮换。非零退出、超时和无效结果默认停下；显式设置 `failover_on_failure=true` 后，才允许有限次通用故障切换。这不叫“已识别配额耗尽”，也不保证下一工具可用。不切换账号、不绕过组织限制、不保证或增加任何服务额度。

各工具从新会话接收统一目标、上次有效摘要和当前工作副本。本包不跨工具导入私有会话数据库，不自动 `resume --last`，不传递隐藏推理，不把 SDK 或云端 Agent 等同于本地 CLI。未安装 CLI 的 GUI 客户端始终由用户进行最终发送和操作。
