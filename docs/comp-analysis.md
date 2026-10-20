# 阵容分析

正式入口：`/runs` 默认进入阵容分析，`/runs?mode=comp` 可明确指定；API：`GET /api/comp/card`。明确的 `mode=list`、单局链接及已有筛选参数的旧列表链接继续进入列表或详情。

## 查询与结果

- 输入 1–5 件物品，按同时包含筛选；支持英雄、段位、最近天数。技能只作为搭配结果。
- 一条真实对局为一个样本；限定当前赛季、当前阶段，剔除无效记录。采集源不含 0 胜局。
- 以 10 胜率为主，同时展示平均胜场和样本量；默认排序使用 Wilson 下界，降低小样本偶然高胜率的影响。
- 按核心物品组合归组；每组只展示其 10 胜样本中的前 8 件补充物品、前 8 个技能，以及最多 3 套真实完整案例。
- 高频物品及技能默认折叠，真实完整阵容默认展开；初始展开不计为用户主动展开事件。分享图始终包含完整搭配与案例。
- 每组支持独立分享链接和 PNG；保留实际品质、附魔和完整技能，二维码指向该组合。生成过程展示组件、卡图、绘制、导出四阶段进度。
- 查询和分享事件说明见 [comp-analytics.md](comp-analytics.md)。

## 实战案例选择

在同一筛选范围、同一核心组合下只使用 10 胜对局，先按英雄和完整物品组合统计精确频次。比较使用卡牌身份，保留重复物品数量，忽略品质、附魔、技能和位置。

从精确频次高的构筑开始归组。替换、增加或减少一件物品允许归为相近阵容；组内任意两套构筑都必须满足该限制，不能通过 A→B→C 连续相似扩大范围。每局仅分配给一个组。

按相近阵容组累计的 10 胜样本数排序，同频时优先本家或中立物品占比高的，再参考时间。归属来自当前 GameData，兼容双龙的 Hero8 标识；未知归属不算本家。

每组代表采用完整物品组合本身出现次数最多的真实构筑，同频再看本家/中立占比与时间，实际展示其最近一局；不拼接品质、技能或附魔。最多取三个组，已选案例的一件物品变体不会再占用一个展示名额。不足三个就显示实际数量。

`similar_ten_wins` 为所属相近阵容组的累计 10 胜次数，`exact_ten_wins` 为代表构筑的完整物品组合次数。页面与分享图展示前者，不能解释为截图完整构筑的精确出现次数。现有胜率、平均胜场、组合排名和前 8 搭配统计保持原口径。

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
python -m pytest tests/test_comp_example_selection.py tests/test_comp_analysis_integration.py tests/test_comp_cache_refresh.py tests/test_runs_metadata_cache.py tests/test_comp_analytics.py tests/test_search_analytics.py tests/test_search_analytics_api.py
python -m unittest discover -s tools/comp_preview -p test_analysis.py
node --test tests/test_comp_share_browser.cjs tests/test_stats_comp_browser.cjs tests/test_search_tracking_browser.cjs
```

浏览器测试需要 Playwright；可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定本机 Chrome。新增、修改、删除战绩，以及没有用户查询时的后台刷新均有回归覆盖。

阵容路线仍暂缓，导航入口保持隐藏。生产继续保留 `ROUTES_MAINTENANCE=1`；该功能不属于本次阵容分析发布范围。
