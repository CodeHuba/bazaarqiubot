# 扎扎空间 Day 快照采集范围（最新用户修正）

用户在开发过程中明确：day 快照只需要取 hour6 PvP 的快照。

该规则优先于此前的“每 Day 最后一场 CombatStart”方案。

- 仅保留 CombatStart 且 hour == 6 且关联 combat.is_pvp 为真的快照。
- 通过 snapshot.eventId == combat.start_event_id 关联战斗后判断 PvP。
- 不可先执行既有 tracker_rsc.extract_combat_snapshots 的按 day 最大 hour 筛选，再筛 hour6；必须从完整 CombatStart 集合中直接挑选 hour6 PvP，否则会漏掉存在更晚 hour 的 day。
- 已有快照复用和页面读取同样筛选 hour6 PvP。
- 某 day 没有符合条件的快照，记录缺失，不以其他 hour 或 PvE 替代。
- 此为扎扎空间需求，不应未经验证改变全局路线原有业务口径。

## 队列与事实共存（最终后端修复）

Zaza 使用显式 `zaza_snapshot_jobs` 和 `zaza_combat_snapshots`，已删除 SQL 字符串替换适配器。旧 `snapshot_jobs`、`combat_snapshots`、`combat_player_cards` 由原全局 worker 管理，Zaza 不 claim、取消或更新这些全局记录。补采事实仅追加到独立表，不替换完整全局事实集合。

复用、公开 Day、day_count 和 available 状态均限定同 run/season/phase、hour=6、is_pvp=1。已有全局合格事实直接复用；全局 pending/processing/retry 时等待旧 worker；全局 success/failed 但只有其他 hour 或 PvE 时独立补采。旧版 Zaza success 没有合格事实时也允许补采。公开读取合并两张事实表，并按 Day 稳定选取真实快照。停止空间继续按停止时间过滤事实。

多空间共享同 run 的 Zaza 队列，事务租约与事实主键提供去重和陈旧回写保护。旧全局 worker 不识别 Zaza 租约，因此无法保证跨业务绝对只请求一次：全局任务在 Zaza claim 后新入队时可能并发抓取；两张事实表隔离保证互不覆盖。旧结果缺少 hour6 PvP 时的一次补采同样必要。全局队列永久停留在非终态时 Zaza 会等待，需由原 worker 恢复处理；没有私自接管全局租约。

## Parser Interface

Backend consumers must call `tools.tracker_rsc.extract_zaza_combat_snapshots(page_source: str) -> list[dict]`. It parses the complete direct RSC or wrapped HTML tracker payload and returns only `CombatStart` facts where `hour == 6` and the combat joined by `snapshot.eventId == combat.start_event_id` has an explicit truthy `is_pvp`. It intentionally does not apply the legacy per-day maximum-hour selection used by `extract_combat_snapshots`.
