# macOS 安装与卸载

需要 Python 3.9+；核心无 pip/npm 依赖。launchd 是可选项。真实 Agent 的安装和登录按各自官方说明完成，本包不下载安装器、不读取认证文件、不购买额度。

先解压并在终端进入 `agent-handoff` 目录，执行本地测试：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

安装到**不存在的新目录**（所有路径支持空格）：

```sh
./install.sh \
  --prefix "$HOME/.local/share/agent-handoff-v1" \
  --state-home "$HOME/Library/Application Support/AgentHandoff"
```

安装器复制代码和文档、创建启动器并初始化本地状态；若安装目录已存在会拒绝覆盖。不会更改 PATH、shell 配置、项目文件或开机服务。调用安装后的完整路径即可：

```sh
"$HOME/.local/share/agent-handoff-v1/bin/handoff" doctor
"$HOME/.local/share/agent-handoff-v1/bin/handoff" list
```

可把 `bin` 目录加入你自己管理的 PATH；本包不自动修改配置。若本机 Codex 仅内置于桌面 App，终端的 PATH 未必含该命令；先按官方 CLI 安装方式配置，再用 doctor 检查。不要凭本次机器上的 App 内部路径配置其他机器。

可选安装个人 Skill（这是第二个全新安装目标示例，不要重复使用已存在的 prefix）：

```sh
./install.sh \
  --prefix "$HOME/.local/share/agent-handoff-with-skill" \
  --state-home "$HOME/Library/Application Support/AgentHandoff" \
  --codex-skill-dir "$HOME/.agents/skills/universal-handoff"
```

`--codex-skill-dir` 仅表示一个由用户明确选择的复制目的地，不会探测或改动其他工具配置。若已完成基础安装，可用文件管理器把 `skill/universal-handoff` 复制到 ADAPTERS.md 列出的个人目录；存在同名目录时先备份，不自动覆盖。无需把 Skill 注入现有项目。

## 先运行本地演示

以下从解压目录执行，状态路径可自行选择，但必须在演示源目录之外：

```sh
python3 handoff.py --home "$PWD/demo-state" init
python3 handoff.py --home "$PWD/demo-state" create \
  --id demo --source "$PWD/examples" \
  --manifest examples/manifest.json --goal-file examples/goal.txt --agents mock
python3 handoff.py --home "$PWD/demo-state" authorize demo --agents mock
python3 handoff.py --home "$PWD/demo-state" run demo
python3 handoff.py --home "$PWD/demo-state" tick
python3 handoff.py --home "$PWD/demo-state" status demo
```

预期：第 1 轮 ready，第 2 轮 review；`demo-state/tasks/demo/workspace/relay-demo.txt` 有本地演示结果。所有模型调用为零。确认结果后可执行 `accept demo --ack-reviewed`。演示文件不是现有项目，源文件不会改变。

## 可选：macOS 定时调度

先用 run 验证一轮，再为已安装路径生成 launchd 文件。生成文件不会加载服务：

```sh
"$HOME/.local/share/agent-handoff-v1/bin/handoff" schedule-plist \
  --output "$HOME/Library/LaunchAgents/local.universal-handoff.plist" --interval 60
plutil -lint "$HOME/Library/LaunchAgents/local.universal-handoff.plist"
```

检查生成的 ProgramArguments、Python 路径、状态路径和 PATH 后，手动启用：

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.universal-handoff.plist"
launchctl print "gui/$(id -u)/local.universal-handoff"
```

每次 tick 全局串行执行最多一轮，已锁住的任务会跳过。launchd 对同一作业不并发启动实例，额外命令仍由锁保护。定时器只运行 ready 任务；没有任务授权不会调用任何服务。用 list/status 查看变化；本版不发送消息或通知。不要用 `sudo`；这是当前登录用户的 LaunchAgent，不是系统 daemon。

StartInterval 不是硬实时保证，睡眠、注销和系统调度会影响触发。用户登录后服务可重新加载持久状态并继续 ready 任务；上次崩溃留下的 running 任务仍需按 OPERATIONS.md 人工检查和恢复。服务不会在用户未登录时运行。Python 或安装路径迁移后，需要卸载旧作业、重新生成 plist 再加载。

停用服务：

```sh
launchctl bootout "gui/$(id -u)/local.universal-handoff"
```

这可能中断本轮；先查看任务状态，等待轮次结束或按恢复流程处理。暂停待运行任务用 `handoff pause TASK_ID`。正在运行或等待人工导入的任务拒绝更改状态，避免假装它已停止。

卸载时先停用服务并确认没有 Agent 写入。通过文件管理器移除该 plist、明确的安装目录和可选 Skill 目录；状态目录包含检查点和成果，建议先备份，再由你决定是否删除。不自动删除其他工具配置、项目或凭据。

## 更新

新版本安装到新 prefix，不覆盖旧安装。先备份状态，用本地 mock 和测试验证；协议版本不兼容时旧状态会停止。关闭旧 LaunchAgent，再基于新安装生成新的 plist。真实 CLI 更新导致二进制哈希变化时，doctor 后重新 authorize。对于脚本启动器，仍需审查其依赖及用户配置；哈希固定不能替代这一点。
