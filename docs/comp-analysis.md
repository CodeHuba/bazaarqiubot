# 阵容分析

正式入口：`/runs?mode=comp`；API：`GET /api/comp/card`。

## 查询与结果

- 输入 1–5 件物品，按同时包含筛选；支持英雄、段位、最近天数。技能只作为搭配结果。
- 一条真实对局为一个样本；限定当前赛季、当前阶段，剔除无效记录。采集源不含 0 胜局。
- 以 10 胜率为主，同时展示平均胜场和样本量；默认排序使用 Wilson 下界，降低小样本偶然高胜率的影响。
- 按核心物品组合归组；每组只展示其 10 胜样本中的前 8 件补充物品、前 8 个技能，以及最多 3 套真实完整案例。
- 每组支持独立分享链接和 PNG；保留实际品质、附魔和完整技能，二维码指向该组合。生成过程展示组件、卡图、绘制、导出四阶段进度。
- 查询和分享事件说明见 [comp-analytics.md](comp-analytics.md)。

## 缓存与更新

`runs_metadata.py` 按映射、翻译、GameData、卡图等文件版本复用卡牌资料，每个查询仍使用独立连接和数据副本。阵容列表的战绩读取直接查询数据库。

`comp_analysis_store.py` 为每个数据库、赛季、阶段维护不可变分析快照。服务启动预热；入库事务提交后调用 `invalidate()`，立即调度后台刷新。每次分析查询检查源版本，后台也每 15 秒检查数据库、WAL 和资源文件变化。没有查询时也会检测外部写入。

后台在独立只读连接中重建，复用未变化的记录；准备完成后一次性发布新引擎。刷新期间已有请求使用旧快照；失败保留上一版并允许重试。同一查询条件的并发计算合并执行。数据未变化时不按固定 TTL 重建；最近天数的时间窗口按分钟推进。

以上为服务端更新机制；现有 HTTP 策略仍允许分析响应缓存 60 秒，并带有 `stale-while-revalidate=300`。浏览器或代理复用响应时，页面可见更新时间还会受到 HTTP 缓存影响，不应将后台 15 秒检查间隔理解为全链路实时保证。

## 运行与验证

需要部署现有卡牌映射、翻译、GameData 和卡图缓存。`web_runs/static/comp-art/` 是可选的生成资源，不纳入 Git；缺少目录时分析回退到卡图资料中的原始地址。补全卡图的来源和缩略图生成逻辑见 `tools/comp_preview/cache_art.py`。

隔离预览使用 `tools/comp_preview/staging.py` 和固定快照；快照及生成图片保存在被忽略的 `.tmp/` 中。预览不写正式战绩或统计。

关键验证：

```sh
python -m pytest tests/test_comp_analysis_integration.py tests/test_comp_cache_refresh.py tests/test_runs_metadata_cache.py tests/test_comp_analytics.py tests/test_search_analytics.py tests/test_search_analytics_api.py
python -m unittest discover -s tools/comp_preview -p test_analysis.py
node --test tests/test_comp_share_browser.cjs tests/test_stats_comp_browser.cjs tests/test_search_tracking_browser.cjs
```

浏览器测试需要 Playwright；可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定本机 Chrome。新增、修改、删除战绩，以及没有用户查询时的后台刷新均有回归覆盖。

阵容路线仍暂缓，导航入口保持隐藏。生产继续保留 `ROUTES_MAINTENANCE=1`；该功能不属于本次阵容分析发布范围。
