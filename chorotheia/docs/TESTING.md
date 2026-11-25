# 测试与验证范围

2026-11-25，macOS arm64、Python 3.9.6。对准备上传的独立源码副本重新运行 **58 项测试，全部通过**，无失败、错误或跳过。测试仅操作临时项目和临时配置目录，使用标准库、本地进程和合成 Agent 输入；不发送真实模型请求。

机器可读记录见 [verification.json](verification.json)。含个人绝对路径的本机安装记录、备份、原生探测输出和项目记忆未公开。

覆盖新旧项目、重复初始化、文档冲突、符号链接与硬链接、路径穿越、并发写锁、原子创建、外部改动保留、进程中断恢复、回滚、敏感模式过滤、Git 暂存/未暂存/未跟踪保护、恢复、接力笔记、协议导出、归档/检索、修复、局部维护升级和安全卸载。临时 Git 提交只用于构造历史夹具。

此前本机正式安装有 31 项冒烟核验通过，正式安装未实际卸载；这不代表原生 Agent 生命周期验收完成。Codex 原生解析识别四事件，但仍需原生信任审阅；Antigravity/Copilot 的真实会话触发与跨服务自动接力未验收。原 Handoff 校验器已接受导出的协议 v1 结果，没有启动控制器。

POSIX 锁仅约束合作写者，不能消除外部编辑器最终检查与替换/删除之间的极小竞态；敏感过滤是启发式；未知旧格式和缺少本机所有权凭据的可移植记忆有明确限制。详见 [操作说明](OPERATIONS.md)。

| # | 测试名称 | 结果 |
|---|---|---|
| 1 | test_actual_process_exit_recovery | passed |
| 2 | test_agent_file_conflict_uses_fallback | passed |
| 3 | test_all_conflict_paths_preserved | passed |
| 4 | test_ambiguous_crash_record_is_preserved | passed |
| 5 | test_atomic_publish_never_overwrites | passed |
| 6 | test_checkpoint_restore_preserves_task | passed |
| 7 | test_cli_end_to_end_commands | passed |
| 8 | test_compact_and_recall_retain_history | passed |
| 9 | test_compare_and_swap_rejects_stale_update | passed |
| 10 | test_crash_journal_blocks_then_recovers | passed |
| 11 | test_cross_process_exclusive_lock | passed |
| 12 | test_dry_run_missing_home_not_created | passed |
| 13 | test_dry_run_zero_project_and_home_writes | passed |
| 14 | test_exclusive_same_bytes_failure_never_deletes_existing | passed |
| 15 | test_explicit_new_refuses_nonempty | passed |
| 16 | test_foreign_agent_namespace | passed |
| 17 | test_full_project_uninstall_removes_only_owned | passed |
| 18 | test_git_dirty_staged_untracked_and_index_unchanged | passed |
| 19 | test_handoff_preserves_existing_controller_and_business | passed |
| 20 | test_history_evidence_separates_unknown | passed |
| 21 | test_hook_auto_init_can_be_disabled | passed |
| 22 | test_hook_git_auto_init | passed |
| 23 | test_hooks_non_git_uninitialized_noop | passed |
| 24 | test_init_external_change_triggers_integrity_rollback | passed |
| 25 | test_lock_symlink_refused | passed |
| 26 | test_manifest_path_escape_refused | passed |
| 27 | test_missing_checkpoint_recovered_from_verified_history | passed |
| 28 | test_modified_manifest_untrusted_ownership_refused | passed |
| 29 | test_modified_memory_blocked_then_explicitly_adopted | passed |
| 30 | test_native_hook_payload_adapters | passed |
| 31 | test_new_init_structure_and_integrity | passed |
| 32 | test_parent_symlink_injection_refused | passed |
| 33 | test_path_traversal_and_symlink_root_refused | passed |
| 34 | test_private_state_outside_repo | passed |
| 35 | test_project_uninstall_preserves_original_and_modified | passed |
| 36 | test_receipt_rolls_back_with_manifest | passed |
| 37 | test_repeated_init_exact_idempotence | passed |
| 38 | test_restore_budget_and_secret_redaction | passed |
| 39 | test_retrofit_preserves_existing_bytes_and_modes | passed |
| 40 | test_rollback_preserves_concurrent_external_edit | passed |
| 41 | test_schema_upgrade_backed_up_and_idempotent | passed |
| 42 | test_sensitive_patterns | passed |
| 43 | test_symlink_leaf_and_hardlink_refused | passed |
| 44 | test_symlink_namespace_does_not_escape | passed |
| 45 | test_transaction_failure_rolls_back_only_ours | passed |
| 46 | test_universal_handoff_protocol_export_exact_shape | passed |
| 47 | test_untrusted_unsupported_protocol_ids_refused | passed |
| 48 | test_existing_hook_configuration_is_not_overwritten | passed |
| 49 | test_global_previews_do_not_open_write_locks | passed |
| 50 | test_global_uninstall_dry_run_zero_change | passed |
| 51 | test_hook_configs_use_native_schemas_and_escaped_launcher | passed |
| 52 | test_install_backups_preserves_configs_launcher_and_sandbox_uninstall | passed |
| 53 | test_install_collision_refused | passed |
| 54 | test_install_dry_run_creates_nothing | passed |
| 55 | test_installer_failure_cleans_new_registrations_only | passed |
| 56 | test_invalid_maintenance_bundle_preserves_installation | passed |
| 57 | test_local_maintenance_upgrade_is_hashed_backed_up_and_idempotent | passed |
| 58 | test_private_home_permissions_enforced | passed |
