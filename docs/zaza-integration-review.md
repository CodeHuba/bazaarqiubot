# 开发中独立审查与最新约束

必须同时读取 docs/zaza-snapshot-scope.md：用户最新要求仅 hour=6 且 PvP 的 CombatStart；不能先按 Day 最大 hour 过滤。全局旧路线 parser 行为保留。

审查发现（代码并发变化中，逐项复核最新版本）：
1. 公开详情必须实际使用 snapshots_path 读取共享 combat_snapshots，run_id + season + phase + hour=6 + is_pvp=1，不能以最终阵容合成。
2. 公开列表/详情应基于成功同步的空间 scope 和成员集合。停用冻结已同步数据；未审核不公开 runs。快照全局暂停与 runs 展示范围应区分：用户要求最新 phase，不能通过请求偷偷写新 scope/兴趣记录；worker负责一致性更新。
3. profile 实际请求必须 /run/profile/<UUID>，校验结构化 profile.id 严格相符。
4. 实际 ingest 保存 raw_json，INSERT没有 profileId列。raw_json.profileId优先；仅raw缺失才兼容独立profileId列。源库只读且筛选season/phase。
5. interest/membership要参与正式读取，不能仅写入无用途。停用后不可直接全量读入新run。
6. 不应claim/cancel旧全局snapshot_jobs改变已有业务。共享快照必须以全局run租约/幂等保障跨业务去重和陈旧回写保护；如隔离任务，须说明跨worker同时采集的处理。
7. 真实 ingest 只接收statWins >= 1；扎扎空间“All runs”仅指现有库已有全部胜场，不保证远端0胜覆盖，不额外抓历史列表。
8. 最新真实网络探测已保存 .tmp/zaza-live-evidence/profile.rsc tracker.rsc summary.json。profile qqr HTTP200；run baf53d51-4586-4bfc-9ced-f73821eaf2b9 HTTP200，旧parser15day、15outcome joins。请以新hour6 PvP口径再次验证。

该记录为开发审查，不表示已部署或验收完成。

## 最终后端核验（2026-10-09）

- profile 验证已改为仅接受结构化 `profile.id` 与 nickname；run 行的 profileId/username 不能充当权威资料。local_runs 和公开读取以 raw_json.profileId 为准，仅原始字段缺失时兼容独立 profileId 列。
- Zaza 已改为显式队列和独立补采事实表；不接管或更新全局 job，也不把 hour6 事实写进旧 max-hour 事实表。复用、公开 Day/count/status 与旧 artifact 兼容读取都筛 hour6 explicit PvP。并发/补采的网络去重限制详见 scope 文档。
- 新增回归覆盖旧 hour9、PvE/未知 PvP success 的补采与原全局 job/facts/cards 不变，全局 pending/processing 保留给原 worker，late enqueue 共存，陈旧租约，暂停/停止，失败退避与耗尽，权威 profile 校验，以及冲突的原始身份优先级。
- 真实网络证据：`.tmp/zaza-backend-final-evidence/summary.json`、profile.rsc、tracker.rsc；qqr canonical profile 与 tracker 均 HTTP200，目标 run `baf53d51-4586-4bfc-9ced-f73821eaf2b9` 实际 hour6 PvP 15 条、Day1–15、15 outcome joins。旧 parser 同样 15 条，但此样本本身不包含更晚 hour；parser 回归另行验证 hour8 不会遮蔽 hour6。
- `.tmp/zaza-backend-final-evidence/probe.py` 使用真实 upstream run 构建隔离 SQLite，并启动实际本地 TCP HTTP；空间详情和 Day 均 200，day_count=15，global_jobs=0，global_facts=0。HTTP 输出保存在 http-detail.json/http-days.json。该验收使用隔离的 19/19.1 scope，未声称当前生产 phase 数据覆盖。
- 聚焦后端验证命令：`python3 -m pytest tests/test_zaza_space.py tests/test_zaza_space_worker.py tests/test_zaza_backend_regressions.py tests/test_zaza_multispace.py tests/test_zaza_snapshot_scope.py tests/test_zaza_integration.py tests/test_snapshot_job_queue.py -q`，实际结果 **38 passed in 0.89s**。
- 未写生产或执行部署；staging 部署仍需父任务完成独立审查后协调，生产必须用户确认。原工作树的大量无关 CRLF/空白差异未改动；仅本任务文件 diff check。
