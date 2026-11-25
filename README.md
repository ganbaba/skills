# Agent Skills

通用 Handoff Skill 与 macOS 本地多 Agent 接力调度器，适配 Codex、Google Antigravity、GitHub Copilot、Claude Code 和 Cursor。

- [通用 Handoff Skill](agent-handoff/skill/universal-handoff/SKILL.md)：跨工具可携带的交接规范与权限边界。
- [系统说明](agent-handoff/README.md)：调度器、五种适配器、统一 JSON 协议、检查点和人工接力。
- [安装与本地演示](agent-handoff/docs/INSTALL.md)
- [实际接口和官方来源](agent-handoff/docs/ADAPTERS.md)
- [安全边界](agent-handoff/docs/SAFETY.md)
- [测试与验证范围](agent-handoff/docs/TESTING.md)

## 开始使用

```sh
git clone https://github.com/ganbaba/skills.git
cd skills/agent-handoff
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
./install.sh \
  --prefix "$HOME/.local/share/agent-handoff-v1" \
  --state-home "$HOME/Library/Application Support/AgentHandoff"
```

Python 3.9+，核心无第三方运行依赖。安装目的地必须是新目录；安装器不会启用后台服务、登录账户或安装其他 CLI。

仅需 Skill 时，可将 `agent-handoff/skill/universal-handoff/` 整个文件夹复制到对应工具的个人 Skill 目录，见适配说明。自动接力还需安装调度器；单独安装 Skill 不会启动服务。

默认只运行本地 mock。真实服务需任务级文件清单和逐服务授权；不自动控制 GUI、读取统一剩余额度、提交或推送原项目。工作副本隔离不等于操作系统沙箱。

初始版本通过 39 项本地测试及临时安装验收；真实模型服务未联调。接口核实日期为 2026-11-24，部署时仍需 doctor 和已授权小样例验收。

## Chorotheia：持久化项目记忆

[Chorotheia v1.0.0](chorotheia/README.md) 提供本地优先、跨 Agent 的初始化、持久化记忆、检查点、恢复、检索、归档和交接。与上面的 Universal Handoff 控制器互补，不替代其任务、工作区与授权协议。

- [Chorotheia Skill](chorotheia/skills/chorotheia/SKILL.md)
- [安装、使用与安全边界](chorotheia/README.md)
- [完整操作说明](chorotheia/docs/OPERATIONS.md)
- [58 项测试及验证限制](chorotheia/docs/TESTING.md)

Python 3.9+，标准库运行。源码包含安装与保守维护工具；不包含本机配置、私有备份、项目记忆或个人路径。原生 Hooks 自动触发与真实跨服务接力仍需在目标环境单独验收。
