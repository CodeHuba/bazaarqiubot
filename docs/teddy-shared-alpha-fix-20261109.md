# 泰迪熊胜率/网站卡图缺主体（2026-11-10 09:28 已发布）

用户验收后明确授权上线，2026-11-10 09:28（北京时间）发布新 WebP，并仅修改生产公共 catalog 中泰迪熊的 artLarge。完整语义比较确认其他卡牌条目不变。刷新 web_runs.service 和 qiubot.service，使网站预热索引与查询缓存重新载入新图片。

- 34 项相关测试通过；生产胜率命令用模拟 QQ API 复测出图，实际读取新本地图片，Alpha 全为 255，没有发送消息。
- 公开 HTTPS `/api/card_search?q=Teddy` 返回新图片 URL，新静态图片 HTTP 200，内容 SHA256 与验收文件一致，240×240 不透明，熊主体完整。网站、机器人、插件加载与 NapCat 连接正常，无启动 Traceback。
- 备份及发布记录：`/opt/qiubot/backups/teddy-shared-art-20261110-012801/release.json`。发布后 catalog SHA256 `02fcb1df9a4a33fc00842b95d77983139b7d55306eabb5d07d02afe905bc4ff9`。
- 生产报告 `/tmp/qiubot-teddy-art-20261109/production-shared-report.json`、`public-check.json`，本地副本 `.tmp/teddy-art-20261109`。
- 生产不存在 `tools/comp_preview/cache_art.py`，因此本次不向生产新增构建工具；缩略图生成器的防复发修复保留在仓库代码中，供后续构建使用。本次未修改正常的 db 查询补图映射或统计数据。
- Git 整理时补入泰迪熊的已核验 CDN 原图、标题与 ArtKey 记录，后续缓存构建优先使用正确原图；另加端到端原生物品回退回归，确认旧透明缓存不会复用。基于最新远端主分支的 35 项相关测试通过。

用户最终澄清：`#bz db` 图片正常，错误发生于 `#bz winrate` 和网站，两处均看不到熊。下文为开发及隔离验证记录，最终发布状态见本文开头。

## 根因与证据

- 生产 RunsQuery._card_image_info 不仅读取 card_images.json，还调用 card_art_catalog.resolve 优先使用 `/static/comp-art/catalog.json` 中的本地图。此前 db 的 monster_sources 覆盖不适用于这条路径。
- 泰迪熊原有本地图为 `b1865925a1cf17d9de9f297a055013fd94f61aa830f75034f661ccc2b9913230.webp`。RGBA，240×240，Alpha 最大 162；图中央已为 `(0,0,0,0)`，熊主体透明且 RGB 已丢失。直接在此坏缩略图上去 Alpha 无法修复。
- 本机游戏纹理 `CF_M_ADV_ViciousTeddyBear_D` 中，中心仍有完整 RGB `(254,196,190)`，但 Alpha 为 0。这是物品材质使用的特效数据，并非网页图像透明度。旧生成器对 RGBA 先 thumbnail，再保存 WebP，导致重采样透明像素时擦除 RGB。

## 修复

- 从已核对卡牌 ID / ArtKey / 材质引用的原始 1024×1024 贴图重建图片，在缩放前转为 RGB，生成不透明 240×240 WebP。
- 新文件 `fe2c35f5a3195cd6645eaad586ae3f1bbd748e63c26dec52a3b625f54b84f405.webp`。仅修改公共 catalog 中泰迪熊一条 artLarge 指向新文件，使用新 URL 避开浏览器长期缓存。不改其他卡牌或 db 覆盖表。
- `tools/comp_preview/cache_art.py` 的 write_thumbnail 新增 opaque 参数。仅原生 Item 纹理回退启用，在 resize 前丢弃材质 Alpha；普通 CDN 图与技能透明图沿用原逻辑。原生 Item 回退缓存键增加 native-item-rgb-v1，防止复用旧坏缩略图。
- 之前尝试在隔离目录补齐 card_images.json 时发现本地 catalog 优先级更高，该尝试未写入生产，也不属于最终发布方案。

## 验证

- 34 项卡图生成、图片刷新和胜率图片相关测试通过，新增零 Alpha 但有 RGB 的原生物品回归，以及技能透明度保留回归。
- 服务器隔离加载生产 RunsQuery、card_art_catalog、胜率命令和图片渲染器，重定向本地 catalog/资产目录，确认返回新 URL，实际载入图 Alpha 全为 255。`#bz winrate 泰迪熊` 成功出图并目视确认熊主体完整，测试使用模拟 QQ API，未发送消息。
- 隔离执行生产网站的索引构建及 card_search 路由（AST 提取，避免启动 Redis/分析线程），查得同一新 URL；隔离静态路由返回 200，响应 WebP 不透明。未修改网站生产进程或任何生产文件。
- 修复预览 `.tmp/teddy-art-20261109/winrate-fixed.png`，网站静态图 `.tmp/teddy-art-20261109/fixed-static/comp-art/fe2c35f5a3195cd6645eaad586ae3f1bbd748e63c26dec52a3b625f54b84f405.webp`。报告 shared-report.json / fixed-asset.json；服务器 `/tmp/qiubot-teddy-art-20261109`。

## 发布注意

本次已获明确上线授权并完成：先上传新图片后原子更新索引，保留所有其他条目及旧文件，刷新预热索引和相关查询缓存，复测网站公开 API 及胜率图片。后续维护继续保留其他并行改动。

检查时公共 catalog SHA256 为 `4a7c4f69fa3a95d61034b78f0179f8cfcfee6631119f2e8627c30126200644b2`，发布前需重新核对。本次不改此前已确认正常的 `#bz db` 图鉴映射。
