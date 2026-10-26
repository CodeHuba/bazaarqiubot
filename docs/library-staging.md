# 图鉴构筑 staging

- 验收地址：https://bazaarqiubot.com/library-preview/
- 初次发布日期：2026-10-26，当时仅授权 staging。2026-10-27 用户验收通过并明确授权生产上线；已发布正式版，见 docs/library-production.md。
- 独立代码：/opt/qiubot-staging/library。
- 独立服务：qiubot-library-staging，监听 127.0.0.1:1034。
- 独立写入目录：/var/lib/qiubot-library-staging；图鉴使用复制出的只读 GameData 和随发布包携带的官方中文及卡图索引。
- Caddy 仅新增 /library-preview/* 代理；正式站导航未增加入口。生产 app.py、runs.html 和 GameData 的发布前后 SHA256 相同。

## 当前范围

1760 条可查询物品及技能（按 Package 隐藏标识排除 90 条包裹）、38 个已支持静态条件的商人。网格仅显示卡图与中文名称；桌面悬停或键盘聚焦时显示单张卡牌的加入按钮，触屏从点击详情中加入，无悬停详情。小／中／大卡图来自原比例资源，宽高比分别为 1:2、1:1、3:2。

包裹不再出现在查询、筛选标签和新增构筑入口。底层卡牌映射仍保留历史条目，避免含包裹的旧草稿整体无法打开；不修改游戏数据库。

图鉴首批渲染 12 张卡牌，每次加载更多追加 12 张，已显示的卡牌不重建。网格图片使用可见区域附近的观察器和最多 4 个并发请求，扣除底部构筑栏遮挡区域；搜索或筛选更新时清理旧图片任务，图片失败后继续加载后续图片。移除密度切换按钮和相关样式／逻辑。

支持筛选、物品站位、技能、品质附魔、本机草稿及图片导出。按用户验收反馈，已移除分享链接按钮、生成和复制弹窗；此前的构筑链接仍可打开，以兼容已有快照。staging 的草稿键和所有资源/API 都带独立前缀。

草稿列表的保存信息仅展示物品和技能数量，不显示英雄信息。

保存草稿按名称、物品站位／品质／附魔及技能内容去重，忽略实例 UID 和数据标签。新建构筑遇到已有同内容草稿时不重复保存；存在未保存修改时仍先保存一份，保存失败则保留当前构筑。历史重复草稿不自动删除。

构筑支持将图鉴物品拖入指定位置、拖动已放置物品调整站位、将技能拖入构筑。拖动时预览落点与相邻物品腾位后的排列，优先贴近落点并减少位移，保持其余物品相对顺序；边缘可向左腾位，满格时吸附到最近可用排列，容量不足或拖出目标区域则保留原构筑。一次放置连同腾位作为一次撤销操作，品质附魔随原物品保留。触屏使用卡牌左上角的拖动把手，普通滑动仍用于滚动；键盘聚焦物品后可用左右方向键调整。

构筑入口改为带箭头和文字的「展开构筑／收起构筑」按钮，收起时使用主题强调色。拖入卡牌自动展开。手机顶部另提供「编辑构筑」入口，展开后保留撤销、重做。

构筑栏已移除物品站位标题、拖动说明文字和右上角构筑英雄选择，不预留这些区域的空白。拖动时继续保留落点／腾位预览；过程状态仅供屏幕阅读器播报，不再显示在栏内。

构筑内卡牌的编辑详情移除底部版本／结算说明、商人来源及移动按钮，底部仅保留移除和保存修改。品质、附魔及卡牌效果照常显示。图鉴查询详情仍保留来源和版本说明。

物品详情的普通标签与隐藏标签逐项显示为带边框的标签；隐藏标签折叠入口也增加边框和数量分隔，保持原有展开／收起行为。深浅主题均沿用站点配色。

按用户验收反馈，构筑栏物品只展示卡图，移除底部名称及预留空间，卡图继续保持原比例；名称仍保留在无障碍标签和点击详情中。图鉴网格及导出图片的名称不变。

导出图片移除构筑名称下的英雄／版本说明行、卡牌名称下的品质文字；物品和技能均用品质色边框表示品质，浅色主题使用更深的边框颜色。附魔改为物品卡图左上角小标记，卡名保留，导出布局同步收紧。

商人筛选仅匹配已支持的静态货池条件；按用户验收反馈，不再提供商人出现天数筛选。商人选择改为可搜索的图文列表，38 个商人均有经 ArtKey 核对的对应图片，选中后保留图片和名称。图片由 staging 独立缓存，加载失败时回退到名称首字。

## 检查与记录

- 本地前缀检查通过：HTML、静态资源、图鉴、详情、卡图均指向 staging。
- 公网 HTML、healthz、CSS、JS、图鉴和黄金寒冰蓝莓派详情均返回 200。
- 公网原图尺寸：烤土豆 200×400、蓝莓派 400×400、烤箱 600×400。
- 公网正式站 /runs 返回 200；服务 active。发布后服务峰值约 237 MiB。
- 浏览器已打开 staging，核对精简网格及点击卡牌详情。后续浏览器自动化超时，未据此声称完整公网交互回归通过；构筑和分享的完整操作在此前本地预览已验证。
- 本地部署清单和检查结果：.tmp/library-staging/。
- 商人图文筛选更新：.tmp/library-staging/merchant-images-update.stdout.log；已核验 38 张图片、本地搜索和选择筛选。仅重启 qiubot-library-staging。
- 拖动腾位验证：node tests/test_library_layout.cjs 通过 399669 种放置检查（容量、边界、不重叠、保序、预览不修改原数据）；本地浏览器实际验证图鉴拖入、物品腾位、品质附魔保留、撤销、拖出取消、键盘移动、技能拖入及收起时拖动自动展开。触屏把手已实现，尚未在真实手机上验证手感。
- 拖动更新记录：.tmp/library-staging/drag-push-update.stdout.log。公网四个前端文件与本地校验一致，HTML 正确加载带 staging 前缀的新布局模块；浏览器已核对新版构筑栏和文字按钮，截图 .tmp/library-staging/drag-builder-staging.png。未改动生产入口或服务。
- 移除分享入口记录：.tmp/library-staging/no-share-update.stdout.log；公网三份前端资源已核验，浏览器确认分享入口消失且构筑和导出仍显示正常，截图 .tmp/library-staging/no-share-staging.png。
- 构筑栏移除名称记录：.tmp/library-staging/board-art-only-update.stdout.log；本地浏览器核验无名称元素、无底部留白，点击仍可打开详情。
- 导出图精简记录：.tmp/library-staging/export-quality-frame-update.stdout.log；本地浏览器实际导出深浅主题 PNG 并目视检查，样图 .tmp/library-staging/export-quality-frame-dark.png 和 export-quality-frame-light.png。
- 构筑栏进一步精简记录：.tmp/library-staging/builder-clean-update.stdout.log；本地浏览器确认两处说明和角色控件移除，并验证拖动、撤销正常。
- 构筑详情精简记录：.tmp/library-staging/detail-clean-update.stdout.log；本地浏览器确认三项内容移除，保存品质附魔正常，图鉴查询详情的来源与说明仍显示。
- 详情标签边框记录：.tmp/library-staging/detail-tags-update.stdout.log；本地浏览器已核验普通标签、隐藏标签展开及深浅主题样式，截图 .tmp/library-staging/detail-tags-preview.png。
- 草稿信息精简记录：.tmp/library-staging/draft-no-hero-update.stdout.log；本地浏览器确认已有草稿摘要仅显示物品与技能数量，截图 .tmp/library-staging/draft-no-hero-preview.png。
- 排除包裹记录：.tmp/library-staging/no-packages-update.stdout.log；核实 90 条包裹均有 Package 隐藏标识。本地浏览器以全部英雄搜索 Package／包裹均为零结果，蓝莓派仍可正常查询。
- 分批与图片加载优化记录：.tmp/library-staging/small-batches-update.stdout.log；node tests/test_library_images.cjs 验证并发、延迟请求、滚出视区、失败、追加、搜索取消、构筑栏尺寸变化和兼容回退。本地浏览器确认首批 12 张、追加后 24 张、搜索重置、图片按需请求及详情正常。
- 草稿重复保存修复记录：.tmp/library-staging/draft-dedup-update.stdout.log；node tests/test_library_drafts.cjs 验证连续保存、保存后新建、恢复草稿后 UID 变化、内容修改、空构筑与保存失败保护。本地浏览器实际打开已有草稿、连续保存两次并新建，草稿数量保持 1，随后撤销恢复测试前构筑；截图 .tmp/library-staging/draft-dedup-preview.png。
- 服务端部署记录：/var/lib/qiubot-library-staging/deployment.json。
- Caddy 备份：/etc/caddy/Caddyfile.library-backup-20261026T092222Z。

## 后续维护

首次安装脚本：tools/deploy_library_staging.py。默认只打印计划；--apply 安装，遇到已有 staging 时拒绝覆盖，更新前应先检查现有文件并备份。

停止本服务使用 systemctl stop qiubot-library-staging。下架预览地址时，仅移除 Caddy 中 isolated library builder acceptance staging 对应的精确路由块，校验后 reload；不要恢复整份旧 Caddyfile 覆盖其他后续配置，不要修改其他预览或生产服务。
