# 使用与故障处理

以下 `handoff` 代表安装后的完整启动器路径；初次阅读先完成 INSTALL.md 的本地演示。

## 创建真实任务

在原项目之外准备一份 JSON 文件清单，例如：

```json
["src/parser.py", "tests/test_parser.py", "pyproject.toml"]
```

只列你已审查并允许复制的文件，不支持 glob、目录或仓库全量导入。还需准备不含密钥的 goal.txt，写清目标、验收标准和禁止的副作用。大项目先整理为小型、干净的可交接任务。

```sh
handoff create --id parser-fix --source /absolute/path/to/project \
  --manifest /absolute/path/to/reviewed-files.json \
  --goal-file /absolute/path/to/goal.txt \
  --agents codex,antigravity,copilot,claude,cursor --max-attempts 6
handoff doctor
```

此时只是本地复制，没有上传。确认每个服务可接收副本内容，并审查其本机工具权限后，再按任务授权**你实际允许使用的服务**。下面示例只授权两个服务，其他候选不会执行：

```sh
handoff authorize parser-fix --agents codex,claude --ack-cloud --ack-local-tools
handoff run parser-fix
handoff status parser-fix
```

每次 run 最多一轮；有效 continue 后，下一次 run/tick 自动选择授权路线中的下一项。其他任务可有自己的服务路线。授权包括后续产生的同一任务副本变更与有效摘要；增加任务范围或导入其他资料应创建新任务重新审查。`--ack-local-tools` 表示已理解副本不是硬沙箱，见 SAFETY.md。

初次执行未授权任务可能进入 `needs_attention/no_authorized_agent`；授权后还需 `resume TASK_ID`，再 run。授权操作不会悄悄解除已有暂停或故障状态。

## 自动保存与调度

每次运行前/后保存快照，运行中默认每 30 秒尝试保存。代码快照保存已写入磁盘的文件；语义进度只在 Agent 返回有效结果时更新，崩溃时不会凭空恢复其未输出的上下文。自动调度依赖已启用的 launchd 或手动 tick，不依赖 Codex App 的聊天调度功能。

`config.json` 位于本地状态 home；可根据 `config.example.json` 修改。仅支持列出的字段，不支持任意命令模板。运行前完整校验配置。

| 配置 | 默认 | 作用 |
|---|---:|---|
| timeout_seconds | 900 | 单轮墙钟上限，之后终止本轮进程组 |
| checkpoint_seconds | 30 | 活跃轮次快照和状态心跳间隔 |
| cooldown_seconds | 300 | 失败 Agent 的本地冷却期，不代表官方额度重置 |
| failover_on_failure | false | 是否在特定已清理的故障后允许有限次继续选下一 Agent |
| manual_agents | [] | 强制人工接力的服务名列表 |
| max_output_bytes | 2097152 | 内存中 stdout 上限；stderr 丢弃，不落原文日志 |

故障切换只接受：非零退出、超时、无效结果/启动失败；且必须完成进程清理和最终安全检查。快照失败、敏感内容、二进制变化、孤儿进程、人工阻塞、预算耗尽均不自动切换。所有候选冷却中时保持 ready，等待后续 tick；不会忙循环。一次 tick 只执行一个任务的一轮，并按最近状态更新时间公平选择。

## 人工接力

缺少 CLI 或 config 中指定 manual 时，已授权候选进入 `awaiting_manual`：

1. 用 status 确认状态；打开 `STATE_HOME/tasks/TASK_ID/workspace`，不要打开原项目。
2. 人工把该任务 `MANUAL_PROMPT.txt` 发给指定工具。它包含本轮唯一 token 与 JSON 格式要求，不要使用旧 Prompt。
3. 不添加原仓库、其他目录或密钥；按自己的权限决策操作 GUI。本包不自动点击、发消息或批准操作。
4. 把工具最终的**协议 JSON 对象**保存成副本和状态 home 之外的结果文件（不含厂商外层 envelope）。
5. 停止该工具本轮任务及后台写入后，明确确认并导入：

```sh
handoff complete-manual parser-fix --result /absolute/path/to/result.json --ack-agent-stopped
```

导入会重新扫描工作副本并核对 task_id/run_id/base_revision；过期结果、重放和跨任务结果会拒绝。用户必须确认工具已停止；没有跨 GUI 的可靠活动状态接口。若人工任务完全取消，可提交一份同 token 的 blocked 结果，内容说明未执行/已取消；之后再 revoke 或调整模式。不要删除锁文件或直接改 status。

如果 CLI 已安装但运行失败，默认进入 needs_attention。先确认没有活动轮次；需要改成人工时，在 config.manual_agents 中添加该服务，修复或重新授权后 resume。若二进制更新，则重新 authorize 获取新的 pin。权限拒绝通常要人工调整任务或服务权限，不通过给全部权限来修复。

## 崩溃与恢复

```sh
handoff status parser-fix
```

若保留了 running 状态：查看记录中的 PGID，并在 Activity Monitor/终端中核对该轮 CLI 和后台任务。自行关停经过确认的本轮进程，不要直接对陈旧 PID 执行 kill。确认没有写入者后：

```sh
handoff recover parser-fix --ack-orphans-stopped
handoff status parser-fix
```

recover 只取得现有锁、确认记录中的进程组不存在、保存当前安全副本，然后转入 needs_attention。它不会杀进程、不重置预算、不自动再跑。若进程组还在或锁仍被持有，则拒绝；若 PGID 缺失，仍依赖你确认孤儿进程已停止。

复核快照与上次摘要的差异、消除部分执行风险后：

```sh
handoff resume parser-fix
handoff run parser-fix
```

SIGTERM/Ctrl-C 会尽力清理本轮进程组并保存；SIGKILL、断电或系统崩溃不能保证执行清理。下一次启动不会抢占 running 任务。不要把重启电脑当成业务动作可安全重试的证据。

## 快照、验收和成果导出

status 中的 baseline 是导入时的内容快照。检查工作副本、runs 中的报告并自行执行可信测试。Agent 返回 done 后状态是 review，调度停止；人工确认后：

```sh
handoff accept parser-fix --ack-reviewed
handoff export-patch parser-fix --output /absolute/path/to/new-changes.patch
```

export 也可在其他非活动状态使用，但不会应用补丁或修改原项目。审查后是否应用、提交由用户另行决定；本包不替你执行这些动作。部分非文本变化会要求人工复制审查，而不会输出不完整的“成功补丁”。

需要还原某个安全快照时，必须确认只覆盖工作副本：

```sh
handoff restore parser-fix --checkpoint PASTE_64_CHAR_HASH --ack-overwrite-copy
```

还原前副本保存在同任务 `workspace-before-restore-*`；还原后 paused，需自行复核再 resume。若机器在还原的目录替换过程中崩溃，先核对这些备份和目标快照，人工恢复 workspace；控制器不会对不完整目录猜测恢复策略。

revoke 清除任务授权并转为 paused；不撤销各厂商账户授权。pause/revoke/accept/restore 都拒绝活动轮次。达到 max_attempts 或快照预算后，保留数据并停止；不要通过随手编辑 state 重置预算继续执行，审查后从干净副本创建新任务。

## 常见状态码

| last_error | 含义与下一步 |
|---|---|
| no_authorized_agent | 未授权；授权后人工 resume |
| cli_pin_changed | 本地程序已变化；doctor、审查、重新授权 |
| unsafe_workspace / checkpoint_failed | 扫描、快照一致性、路径或容量问题；检查本地副本，不放宽密钥拦截 |
| provider_exit_nonzero | CLI 失败；手动在该工具检查登录/政策/权限，不推断剩余额度 |
| invalid_result_or_launch_failure | 输出协议不符、厂商失败 envelope 或启动失败；检查适配或转人工 |
| timeout / output_limit | 单轮耗时或 stdout 太多；检查是否挂住，不盲目增加权限 |
| orphan_processes | 可能有残留进程；停止接力并按恢复步骤检查 |
| attempt_budget_exhausted | 本任务预算用完；审查后建立新的受限任务 |
| recovered_requires_resume | 现场已保存，尚需人工复核与 resume |

CLI 返回码 0 表示本次控制器操作已处理，任务本身可能是 review 或 needs_attention，务必读取 JSON 的 status；2 为配置/安全/输入错误，3 为锁占用，130 为外围中断。无人值守系统应检查状态，不只检查命令返回码。
