# 阵容分析埋点

后台入口：`/admin/stats#features` →「阵容分析 · 查询与分享」，跟随全局 1/7/30/90 天筛选，按北京时间自然日统计。

## 三类数据

| 来源 | 统计含义 | 排除项 |
| --- | --- | --- |
| `search_event`, feature=`comp_card` | 用户主动提交查询、UV、结果分类、选择物品数量 | 自动链接加载、排序；相同匿名访客相同完整条件 10 秒内去重 |
| `comp_event` | 进入分析、排序、展开案例、打开对局、分享打开/复制/生成/下载/落地 | 普通阵容列表分享不计入；上报重试按事件 ID 去重 |
| `api_log`, endpoint=`/api/comp/card` | 实际到达服务器的请求、异常与平均响应耗时 | 浏览器缓存命中不产生服务器请求；请求量不是主动查询量 |

新版查询凭据使用接口实际解析后的 `anchors` 和 `scope`。无组合但 baseline.count > 0 计为样本不足；存在推荐组合计成功；无对局计无匹配。历史数据不做推算回填。

## 交互协议

`POST /api/track/comp`，JSON：`id`, `action`, 可选 `group`, `detail`, `duration_ms`；请求头 `X-Fingerprint` 使用已有匿名访客标识，数据库存储 SHA256。事件 ID 幂等，网络/5xx 只重试一次并复用 ID；失败不阻塞查询与分享。

- `view`：从列表进入分析，或直接进入分析模式。
- `sort`：点击排序，detail 为 reliable/rate/appearance。
- `examples_open`：展开某组合的真实完整阵容。
- `run_open` / `source_open`：点击打开本站对局/原始对局。
- `share_open`：分享弹窗打开。
- `share_link_copy` / `share_image_copy`：Clipboard API 写入成功。
- `share_generate`：开始生成，包含重新生成。
- `share_generated`：PNG Blob 已成功生成并展示，detail 为 complete/missing_art，记录耗时。
- `share_error`：生成失败，detail 为 prepare/art/render/encode，记录耗时。
- `share_cancel`：生成过程中关闭弹窗，记录耗时；不重复计失败。
- `share_download`：点击下载链接，不代表操作系统实际保存成功。
- `share_landing`：自动查询后实际定位到分享组合，不代表生成者主动查询。

分享链接同时加 player_share/share/comp 来源标识，复用访问来源统计。每个匿名访客每分钟最多 180 个新事件，body 最多 2048 字节；事件保留 90 天，每 UTC 日清理一次。

`GET /api/stats/comp?days=7`：复用后台 Basic Auth，响应 private/no-store。只返回聚合数，不返回访客 ID。未采集的历史交互不回填。离开页面、浏览器拦截、跨期生成可能使生成起止数量不一致，界面不把它们换算成精确转化率。

## 验证

后端：`tests/test_comp_analytics.py`, `test_search_analytics.py`, `test_search_analytics_api.py`, `test_web_analytics.py`。
浏览器：`test_comp_share_browser.cjs`, `test_stats_comp_browser.cjs`, `test_search_tracking_browser.cjs`。
覆盖鉴权、匿名哈希、幂等/重试、限流、保留期、北京时间边界、查询与请求分离、完整分享图成功/占位/失败/关闭、后台移动布局与失败重试。
