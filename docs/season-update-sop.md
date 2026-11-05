# BazaarQiuBot 赛季更新 SOP

本流程适用于 The Bazaar 同赛季补丁更新（如 `19.2 → 19.3`）和大赛季更新（如 `S19 → S20`）。线上目录固定为 `/opt/qiubot`，本地权威仓库为 `D:\PJ\QiuBot-github\QiuBot`。

2026-10-21 根据 19.2 执行记录、近期已合并发布记录和线上只读检查补充。2026-10-24 用户确认 19.3 的实际切换时间为当天北京时间 08:00，即 UTC `2026-10-24T00:00:00+00:00`；此前 10 月 22 日的预估不作为执行依据。

## 更新原则

- 当前在线 runs 库只保存当前 `season/phase`，历史数据库完整归档到 COS。
- 不给旧 runs 批量改标签。每次 phase 切换都创建一份空的在线 runs 库。
- `GameData.db` 与 `zh-CN.bytes` 必须来自同一个运行时 cache 目录。
- 卡图 CDN 的 `z<version>` 不等于 phase。必须实测 CDN 后再改，不能把 `18.2` 自动当成 `z18.2`。
- 公告只创建草稿，由管理员审核后手动发布。
- Git 只从本地权威仓库提交，服务器工作树不是提交源。
- 遵守 `AGENTS.md`：网站改动先提供验收，用户验收通过并明确同意上线后才部署。回顾 SOP、同意方案、允许开发、同意 Git push 均不等于生产更新授权。
- 阵容路线继续暂缓，导航入口保持下线，保留生产 `ROUTES_MAINTENANCE=1`。新版阵容分析、阵容列表和分享不属于阵容路线功能。
- 主站、Windows 普通采集器、扎扎空间有独立进程和配置；禁止假设改一处 phase 会同步所有服务。

## 0. 更新前准备与验收

### 0.1 记录参数和发布范围

每次先记录目标 phase、实际发布时间 `T0`（北京时间及 UTC）、新资产 SHA-256、当前 Git 基线、线上文件差异、服务与计划任务状态、真实数据库路径、备份目录、COS 对象键和验收结果。T0 未确认时，只准备，不执行切换。

同赛季补丁的参数分工：

| 参数 | 19.2 → 19.3 的处理 |
| --- | --- |
| `CURRENT_SEASON_ID`、`RUNS_SEASON_ID` | 保持 19 |
| `CURRENT_PHASE` | 改为 `19.3` |
| `PLAYER_STAT_SEASON_ID` | 保持当前实际赛季加一，即 20；不能跟着补丁号变化 |
| Windows `SEASON_START` | 不因同赛季补丁修改 |
| Windows `PHASE_START_UTC` | 改为已确认 T0 的 UTC 时间 |
| `CARD_IMAGE_VERSION` | 独立实测，不能直接填 `19.3` |
| 扎扎 `ZAZA_SEASON/PHASE/PHASE_START` | 单独确认是否纳入此次切换，不能仅改主站配置 |

从当前已合并发布基线准备补丁，保留近期阵容分析、分享、卡牌预览、主题和埋点功能。2026-10-24 准备时远端 main 基线为 `a13c04c`，已包含 SQLite 并发修复和查询资源保护；执行日重新核对。当前本地分支可能落后且混有未提交改动，不能整目录覆盖生产或直接上传整份工作区文件。

维护公告和恢复公告先准备为草稿，审核时确认版本、维护影响、预计恢复时间；只有管理员明确要求发布时才发布。发布授权与代码上线授权分别按实际指令记录。

### 0.2 核对目录、容量和实际进程

2026-10-20 备份目录已迁移到数据盘，原路径保留符号链接：

| 兼容路径 | 2026-10-21 实际位置 |
| --- | --- |
| `/opt/qiubot/backups` | `/data0923/qiubot/storage/backups/qiubot-backups` |
| `/opt/qiubot/data/bazaar_runs.db` | 系统盘原路径 |
| `/opt/qiubot/plugins/bazaar_plugin/cache` | 系统盘原路径 |

```bash
mountpoint /data0809
mountpoint /data0923
readlink -f /opt/qiubot/backups
readlink -f /opt/qiubot/data/bazaar_runs.db
df -h /opt/qiubot/data /opt/qiubot/backups /opt/qiubot/plugins/bazaar_plugin/cache
systemctl is-active web_runs.service qiubot.service
```

分别校验在线库所在盘、备份盘和资产暂存盘的空间与写权限。当前脚本变量虽叫 `ROOT_AVAIL`，实际检查的是 `BACKUP_ROOT` 所在盘，不能据此判断系统盘也有空间。容量还要覆盖 WAL、缓存备份和资产生成；不要覆盖兼容符号链接或清空整块数据盘。详见[服务器数据盘分工](https://github.com/CodeHuba/bazaarqiubot/blob/ae291ef/docs/server-storage.md)。

读取 Windows 计划任务 Actions 和实际运行文件，核对 Chrome 主版本、最近成功同步时间、异常日志。普通采集任务已知运行 `D:\PJ\QiuBot\tools\run_collector.bat`，并非权威仓库目录；不能只改仓库中的同名脚本。任务显示 Running 不代表采集成功。

清点读写 runs 库的 Web、机器人、后台刷新、外部导入和独立 worker。2026-10-21 只读检查遇到 `database is locked`，锁归属为当时的 Web 主进程；此记录不等于已诊断锁的成因。维护窗口内需要停止相关写入并确认锁已释放，再备份和换库，不要强删 WAL 或 journal 解锁。

### 0.3 先做隔离预演和额外备份

- 核对线上与待执行的 `season_update.sh`、`season_archive_runs.py`、`rebuild_gamedata_caches.py`，检查 shell 语法和 `--help`。19.2 曾因线上脚本落后而缺少缓存重建步骤。
- 对暂存的两个 SQLite 文件做只读校验，核对 cards/translation 表、卡牌与翻译键匹配、哈希和来源；文件修改时间不能证明版本正确。
- 将新资产的映射与翻译先重建到独立临时目录，`--cache-dir` 也指向该目录，避免预演清掉生产单卡缓存。
- 原子替换文件必须同时保留原属主、属组和权限。用 root 创建临时文件后仅复制 chmod 再 rename，会使原属 ubuntu 的 0600 索引变为 root 专有，服务无法读取；先记录 stat，替换前 chown/chmod，再以实际服务用户核对可读性。19.4 的主站卡图索引曾出现此问题，已按备份恢复属主并通过公网复验，无需放宽文件权限。
- 列出当前 runs 库 schema，与 `season_archive_runs.py` 的允许表集合比对，并在临时目标库演练。新增表必须先分类处理，不能通过删表绕过校验。
- 除脚本自带备份外，提前备份 `card_id_mapping.json`、`translations.json`、`card_images.json`、待变更的卡图/附魔等本地资源索引、`bazaardb_card_*.json`、`web_runs/static/comp-art/catalog.json` 及其关联图片；记录环境和计划任务状态，敏感配置只保存在服务器受限备份中。
- 在隔离环境验收新资料、空库和少量真实新版样本；日期相关测试要随已确认 T0 调整。取得明确生产更新授权后才进入第 2 节。

### 0.4 大赛季切换的额外参数与验收

`S19.4 → S20.0` 必须使用 `season 20 20.0` 模式，不能只执行 `patch 20.0`。主站 `CURRENT_SEASON_ID` 和 `RUNS_SEASON_ID` 同步改为 20，`CURRENT_PHASE` 为 `20.0`；`PLAYER_STAT_SEASON_ID` 保留 `CURRENT_SEASON_ID + 1` 表达式，得到 21，并以新版上游实际返回复验。不要把界面 `S20.0` 直接写进数据库 phase。

实际 Windows 采集器的 `SEASON_START` 和 `PHASE_START_UTC` 均须以已确认的新赛季 T0 生成，前者为 GMT 字符串、后者为带时区 ISO 时间。仓库副本与实际副本可能不同，只对预检哈希匹配的实际副本作最小修改，保留认证恢复和分页断点逻辑。首次采集应显式覆盖请求范围到 T0，补采到边界后恢复四小时滚动窗口；不要依赖默认窗口找全延迟维护期间的数据。

新库只保留 `(20, '20.0')`，不携带 S19 对局、快照和任务。历史库完整归档并复核 COS；现有本地 ID 库不清空。如旧库已采到 T0 后的合规对局，按第 4.1 节校验后通过 ingest 回填，不能整库改标签。

主站与独立卡牌库分别更新同源资产、派生资源和标签；CDN 独立实测，不能将 `z20.0` 视为必然存在。职业概况、卡牌榜、T 表、胜率、阵容分析、分享图和卡牌详情必须显示 S20.0/20.0，默认筛选应为 `S20.0 全部`；近 N 天仍受当前 phase 限制。玩家查询、图片报表、订阅/监控与近期副英雄事件功能需要回归，赛季专属缓存/持久化数据逐项核对，不能清空订阅、用户设置或历史报表来完成更新。

### 0.5 QiuBot 游戏资料查询同步更新（与主站同批验收）

S20.0 更新范围明确包含 **QiuBot 本身的游戏资料查询资源**，不止网站统计与独立卡牌库。新版文件到位后执行以下检查，当前准备阶段不替换正在使用的 S19.4 资料。

- 核对 `qiubot.service` 的实际工作目录、启动入口和生效 `GAMEDATA_DB`，确认机器人使用与本次验收同哈希的 `GameData.db`。`BazaarDataClient.bootstrap()` 会将物品/技能载入内存，不能以网站卡牌预览更新成功代替机器人更新成功。
- 同步名称翻译 `cache/translations.json`、tooltip 所需 `zh-CN.bytes` 和 `data/card_id_mapping.json`，重建到隔离目录后核对新增/改名/删除卡牌。当前机器人 `translations.py` 优先检查 `ZH_TRANSLATIONS_DB`，再检查 `cache/zh-CN.bytes` 和运行时目录；分别核对 Web 与机器人进程实际生效的配置及优先命中文件，避免旧副本遮蔽新翻译。
- 同步 `cache/card_images.json` 及相关游戏插图、技能图、商店/野怪/事件资源索引；核对原生 ID、类型和 ArtKey。对于野怪/事件的补充来源表、百科天数及插画，验证新版本适用性，不把旧赛季资料当成新事实；无法确认时保留缺失或来源提示，不猜图、不全盘删除有效图片。
- `bz refresh` 不能替代资源安装及进程重载：现有 `refresh()` 会跳过内存中已有的物品/技能，且名称、tooltip 和遭遇名称存在进程内缓存。获准上线安装后按 SOP 重启实际 `qiubot.service`，确认从新版 GameData 启动加载成功，不能接受异常后静默回退旧资料。
- 单独核对 `merchants.json.gz`、`encounters.json.gz` 等辅助缓存的来源及版本；这些不由 `rebuild_gamedata_caches.py` 全量重建。重新获取前先备份，若上游尚未更新则标记限制，不能用“已重建映射”代替辅助资料验收。
- 验收物品、技能、品质/附魔、英雄适用范围、野怪阵容/掉落、商店商品/费用与刷新规则、事件分支奖励、副英雄事件等查询；优先选 S20.0 新增及效果变化样本，逐项对照原生资料。中英文搜索、同名跨类型候选、图片分页、图片失败文字回退均需检查。
- 保留现有玩家赛季查询、胜率/统计图片、订阅/监控能力；只处理此次版本相关资源和缓存，不清空订阅、用户数据或历史报表。消息渲染与上传使用模拟事件验收，不向 QQ 群或私聊发送测试消息。
- 验收记录分别列出机器人资源路径/哈希、启动加载数量、实际查询样本及结果、缺图或上游滞后限制；完成后才将 QiuBot 游戏查询标记为 S20.0 已更新。

## 1. 准备数据文件

权威来源：

```text
C:\Users\Administrator\AppData\LocalLow\Tempo Storm\The Bazaar\prod\cache\GameData.db
C:\Users\Administrator\AppData\LocalLow\Tempo Storm\The Bazaar\prod\cache\translations\zh-CN.bytes
```

在 Windows PowerShell 上传到服务器暂存目录：

```powershell
scp "C:/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/GameData.db" ubuntu@101.34.58.217:/home/ubuntu/
scp "C:/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes" ubuntu@101.34.58.217:/home/ubuntu/
Get-FileHash -Algorithm SHA256 -LiteralPath "C:/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/GameData.db","C:/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes"
```

WSL 中的对应上传命令：

```bash
scp "/mnt/c/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/GameData.db" \
  ubuntu@101.34.58.217:/home/ubuntu/
scp "/mnt/c/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes" \
  ubuntu@101.34.58.217:/home/ubuntu/
```

服务器用 `sha256sum /home/ubuntu/GameData.db /home/ubuntu/zh-CN.bytes` 与本地比较；安装后再次比较目标文件。确认两个 SQLite 文件可读、版本相互匹配。不要使用 Steam 安装目录中的同名 GameData。还需确认实际生效的 `GAMEDATA_DB`、卡牌预览的 `ZH_TRANSLATIONS_DB` 等显式路径没有指向其他副本。

## 2. 执行更新

以下为获准上线后的命令模板，时间占位符必须替换为已确认值。同赛季补丁：

```bash
ssh -t ubuntu@101.34.58.217 \
  'cd /opt/qiubot && ./tools/season_update.sh patch 19.3 "<已确认的北京时间 YYYY-MM-DD HH:MM:SS>"'
```

大赛季命令格式（赛季号、phase 与时间均按实际填写）：

```bash
ssh -t ubuntu@101.34.58.217 \
  'cd /opt/qiubot && ./tools/season_update.sh season <新赛季号> <新phase> "<已确认的北京时间 YYYY-MM-DD HH:MM:SS>"'
```

只有明确复用已安装 GameData 和翻译时才使用 `--skip-assets`。

执行前先暂停普通采集计划任务，记录原状态；核对其他写入者并在维护窗口内冻结写入。当前脚本只主动停止 `web_runs.service`，不会自动暂停 Windows 任务或独立 worker。不要让仍加载旧 phase 的进程在换库后写入。

脚本会完成：

1. 校验依赖、上传文件和磁盘空间。
2. 停止 `web_runs.service`，冻结在线数据库。
3. 使用 SQLite backup API 创建完整历史副本。
4. 上传 COS，并按对象大小验证归档结果。
5. 创建仅含 schema、索引和新 phase 元数据的空在线库。
6. 安装新的 GameData 与翻译。
7. 原子重建 `card_id_mapping.json`、`translations.json`，清理 `bazaardb_card_*.json`。
8. 更新 `CURRENT_SEASON_ID`、`RUNS_SEASON_ID`、`CURRENT_PHASE`。
9. 清理 Python 字节码，使用 systemd 重启网站与机器人并做健康检查。
10. COS 验证成功后删除本地大体积 runs 回滚副本，保留配置和资产备份。

当前脚本在中途失败时尝试恢复其备份的配置、原始资产和在线 runs 库；它不会自动恢复所有派生缓存、Windows 采集器、扎扎配置或 Redis。不得将其视为完整事务回滚，补充处理见第 6 节。

脚本完成不代表全部验收完成：它不更新 Windows phase 边界、不切换扎扎、不生成分享图目录、不清理所有外部缓存，也不发布公告。保留日志，继续执行下列步骤。

## 3. 卡图版本

先从现有 `card_images.json` 取一条图片 URL，用带浏览器 UA/Referer 的 GET 请求测试旧版和候选新版：

```bash
python3 - <<'PY'
import requests
urls = {
    "old": "https://s.bazaardb.gg/v1/z19.0/<hash>@256.webp?v=8",
    "candidate": "https://s.bazaardb.gg/v1/z19.3/<hash>@256.webp?v=8",
}
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://bazaardb.gg/"}
for name, url in urls.items():
    response = requests.get(url, headers=headers, timeout=15, stream=True)
    print(name, response.status_code, response.headers.get("content-type"))
PY
```

至少抽查物品、技能、新增或改名卡牌以及两个常用尺寸。只有候选版本返回 `200 image/webp` 后，才通过 `CARD_IMAGE_VERSION` 或代码默认值切换并运行 `fetch_card_images.py`。如果候选返回 404，继续使用已验证可用的旧 CDN 版本；19.2 使用 `z19.0`，19.3 仍需重新验证。

近期新增的图片查询修复必须保留：先尝试公开标题，再尝试 InternalName，并核对返回卡牌的原生 ID 和类型，防止同名卡、技能、内部名差异造成错图。刷新失败保留已有有效记录，不通过拼接版本、哈希或替换卡牌 ID 猜图。

### 3.1 卡牌预览和分析分享图片

- 卡牌悬浮预览使用当前 GameData、翻译与卡图；验收 `/api/card-preview/<真实card_id>` 的 `dataLabel`、正文、品质及附魔，而非只看 HTTP 200。重点检查新增/改名/效果变化卡牌。
- `web_runs/static/comp-art/` 是生成资源，生产绝对路径为 `/opt/qiubot/web_runs/static/comp-art/`。核对 `catalog.json` 和本地图片的真实存在性、卡牌身份及新资料一致性；旧 catalog 的名称、尺寸等字段可覆盖新查询资料，不能只更新 GameData 就认为它已同步。
- `tools/comp_preview/cache_art.py` 是辅助生成器，要求独立 `state/runs.db`、输入卡图目录和 native DB，输出 `state/card-assets.json` 与 `state/art/`；它不直接安装生产 `comp-art/catalog.json`。先在隔离目录生成、校验并按已验收的发布流程安装，禁止把生产目录随意当成 `--state`。
- 对真实对局中的物品和技能检查缺图报告；只有可验证的官方地址或原生贴图映射才能补图。缺图、超时应有占位或可解释提示，不能卡住整张分享图。
- 分享图片、卡牌预览和分析响应涉及独立 HTTP 缓存。核对版本/ETag 与资源 URL，必要时在授权发布中更换资源标识；不清空全部 Redis 或删除所有图片。

### 3.2 独立卡牌库服务（2026-10-29 补充）

卡牌库与构筑于 10 月 26 日上线后使用独立 `qiubot-library.service`（回环端口 1035），工作目录 `/opt/qiubot-library/current` 指向版本目录。它读取自己的 `input/GameData.db`、`input/zh-CN.bytes`、`input/card_images.json`，不跟随主站 cache 自动更新。发布前只读核对当前链接、三个输入文件哈希、`tools/library_wsgi.py` 与 `tools/preview_library.py` 哈希及 `/healthz` 的 version/fingerprint。

将卡牌库数据同步纳入本次主站版本验收，先用同一对新资产提供独立图鉴与构筑预览。获准上线后，额外备份独立输入文件、当前链接、版本标签配置和受影响的图片缓存；保留原部署结构和只读权限，按验收候选安装资产，通过受控的 `LIBRARY_DATA_LABEL` 环境配置设置新 phase，再重启独立服务。不能只重启主站，不能整目录覆盖已经上线的图鉴代码，也不要改动旧 staging 服务。

核对 `/library/api/library` 的 version、fingerprint、卡牌数和 skipped，抽查新增、改名、效果变化卡牌及品质、附魔、构筑。`/var/lib/qiubot-library/images-native-1` 按 card ID 缓存图片；同 ID 的 ArtKey/原生身份变化时，需要单独核对并失效受影响的缓存，而非全删图片。出现日期另有观察来源和版本，不能把旧 phase 的观察记录当成新版本重新验证的结论。

## 4. 恢复采集与验收

大赛季更新后，先修改 Windows 采集器的 `SEASON_START`；同赛季补丁不改。每次 phase 切换还需同步实际运行的 Windows 采集器 `PHASE_START_UTC`；默认 `createdAfter` 取最近四小时窗口起点与 phase 起点中较晚者。上游接口可能忽略此参数或返回更早创建的记录，不能仅依赖查询参数。

当前已确认的业务口径为按创建时间切分，采集器在推送前再次校验 `createdAt >= PHASE_START_UTC`，并拒绝缺失、无时区或无法解析的创建时间。显式设置 `BAZAAR_RUNS_CREATED_AFTER` 只改变请求范围，不绕过入库校验。如要改为按完成时间归属，需要另行确认。然后触发 runs 采集。

验收至少包括：

```bash
ssh ubuntu@101.34.58.217 'cd /opt/qiubot && \
  sudo systemctl is-active qiubot.service web_runs.service && \
  grep -nE "CURRENT_SEASON_ID|CURRENT_PHASE|RUNS_SEASON_ID" plugins/bazaar_plugin/data_client.py && \
  venv/bin/python -c "import sqlite3; c=sqlite3.connect(\"data/bazaar_runs.db\"); print(c.execute(\"pragma integrity_check\").fetchone()[0]); print(c.execute(\"select season,phase,count(*) from runs group by season,phase\").fetchall())"'
```

还要确认：

- `/api/heroes`、`/api/runs`、`/api/hero_overview?rank=legendary` 返回 200。
- 职业概况不再显示赛季初缓存的空结果；ingest 新数据会清理职业概况与 T 表缓存。
- 数据时间范围符合已确认的业务口径。当前按创建时间切分，应确认没有早于 phase 起点的记录。清理已有数据前，先确认口径并备份，不要直接删库。
- COS 对象大小与本地归档一致。
- `card_id_mapping.json`、`translations.json` 数量合理，单卡旧缓存已清理。
- 公告标题、正文中的 phase 正确，并保持 `draft`，由管理员手动发布。

### 4.1 先校验边界，再恢复定时采集

在修改实际 Windows 采集器的 `PHASE_START_UTC` 后，先用少量样本校验 T0 前、T0 恰好、T0 后、显式时区和无效时间的行为；T0 为包含边界。检查实际运行副本中的 `run_in_current_phase()` 和推送前过滤仍存在，不能仅检查 `createdAfter` 请求参数。

新库确认 phase 元数据、脚本服务健康后，触发一次采集；验证最早/最晚创建时间、早于 T0 的数量为 0、只含目标 season/phase，再恢复原有定时状态。保留最早创建时间和采集完成日志。若发现越界记录，先暂停来源、备份当前库，再按已确认口径清理关联数据，不能直接重新运行整个换库脚本。

本地已采集 ID 库与在线 phase 库的用途不同：本地可能已经见过仍需导入新 phase 空库的合规对局，不能用本地去重记录跳过本次回填；本地 ID 新增量也不等于在线本期入库量。以服务器入库响应和数据库 scope 计数为准，不为消除计数差异清空本地历史。

若执行时间距离 T0 已超过四小时，首轮默认滚动窗口不能覆盖整个新 phase。先完整归档旧库，单独导出其中创建时间合规的新版候选记录，在隔离环境验证后按既有 ingest 规则回填新库，不能直接修改旧库标签。首轮采集进程临时设置 `BAZAAR_RUNS_CREATED_AFTER` 为 T0 的 RFC 1123 UTC 时间，必要时临时调整已有的 `BAZAAR_RUNS_MAX_PAGES`，确认分页是否完整结束；不能把页数上限当成已补齐。补采结束后恢复原计划任务与默认四小时窗口，保留回填数量及最早时间；未回填的时间缺口须明确记录。

长时间补采可能遇到上游会话过期，保留既有认证恢复逻辑，并记录最后成功的时间/ID 游标。自动恢复失败时，保留已确认入库数据，在新会话中按同一个 T0 和可证明已遍历到的游标续采，以正常 ingest 的 ID 去重重放重叠部分；不能按页号估算后直接跳过，也不能把异常退出当成完成。19.4 在约 15 分钟后遇到此问题，续采仅使用临时执行器在内存中设置初始游标，不修改已安装采集器的认证与重试逻辑。

以下为恢复后的只读检查，边界直接来自 `phases.start_time`，避免在验收命令里另写一份错误日期：

```bash
cd /opt/qiubot
venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('file:/opt/qiubot/data/bazaar_runs.db?mode=ro', uri=True, timeout=5) as c:
    print('integrity:', c.execute('PRAGMA integrity_check').fetchone()[0])
    print('phases:', c.execute('SELECT season,phase,start_time FROM phases').fetchall())
    print('scope:', c.execute('SELECT season,phase,COUNT(*),MIN(created_at),MAX(created_at) FROM runs GROUP BY season,phase').fetchall())
    bad = c.execute('''SELECT COUNT(*) FROM runs r LEFT JOIN phases p
        ON p.season=r.season AND p.phase=r.phase
        WHERE julianday(p.start_time) IS NULL OR julianday(r.created_at) IS NULL
           OR julianday(r.created_at)<julianday(p.start_time)''').fetchone()[0]
    print('invalid_or_before_phase:', bad)
    assert bad == 0
PY
```

该 SQL 检查边界与可解析性；没有显式时区的输入仍由采集器拒绝。全库旧 phase 残留另按 scope 输出判定。统计数据库有无效职业或其他剔除项时，API 样本量可能小于原始 runs 数，不能机械要求所有数字相等。

### 4.2 新版阵容分析、缓存和页面验收

2026-10-20 起正式 `/runs` 默认进入阵容分析，不能只验收旧的三项 API：

| 验收对象 | 必查行为 |
| --- | --- |
| `/runs`、`/api/comp/card` | 当前 phase 为目标版本；空库、小样本、无组合均有合理结果，无 `min_support` 错误或 500 |
| `/runs?mode=list`、单局详情 | 普通查询正常，完整物品和技能、实际品质与附魔一致 |
| 阵容分析及列表分享 | 链接可回到对应模式/组合，PNG 有完整物品、技能、品质和附魔；图片准备不能导致长时间卡死 |
| 卡牌悬浮预览 | 新版效果/名称正确，品质和附魔可切换，标签为目标 phase |
| 职业概况、T 表、胜率 | 有效职业范围正确，`Common` 等异常职业不参与职业概况，总数与当前筛选下的有效样本一致 |
| 明暗主题、后台统计与埋点 | 已上线功能未被旧文件覆盖；访问和交互统计库不因换 runs 库而被重建 |
| 阵容路线 | 继续维护/下线，导航入口不恢复；不为验收而启用队列或重建路线 |

缓存分层处理：

- Redis 胜率列表按 `winrate_cache:<season>:<phase>` 分隔；新 phase 不复用旧 key。同一 phase 内清理数据后，需针对该 key 清理并重建，校验与有效入缓存记录一致，不使用全库 flush。
- 职业概况、T 表、旧阵容榜缓存要重新计算，首批入库后不能一直显示空库结果。
- 新 `comp_analysis_store.py` 按真实 DB 路径、season、phase 管理分析快照；启动预热，ingest 提交后 invalidate，后台每 15 秒检查 DB/WAL/资源变动。10 月 23 日资源修复已使常规重建至少间隔 60 秒，并合并采集期间的连续变化；冷启动可暂时返回带重试提示的 503。确认新 phase 的引擎已经就绪，刷新失败不能以旧 phase 快照冒充成功。
- `runs_metadata.py` 对映射、翻译、GameData、卡图、职业与资源索引做版本识别；卡牌改名、尺寸和职业归属都要使用新版资料，包括双龙的 Hero8 映射。原始资产更新、派生 JSON 更新和后台分析刷新是不同步骤。
- 分析接口仍允许 HTTP `max-age=60`、`stale-while-revalidate=300`；15 秒后台检查不等于浏览器 15 秒内必然更新。跨 phase 切换使用未缓存请求/新会话对比，必要时按已批准的代理缓存方案失效，不能仅因本机强制刷新正确就宣布全部恢复。

### 4.3 扎扎空间单独核对

2026-10-21 的线上只读快照：主站是 19.2，`/etc/qiubot-zaza-staging.env` 仍为 `ZAZA_SEASON=19`、`ZAZA_PHASE=19.1`、`ZAZA_PHASE_START=2026-10-08T01:00:00Z`；Windows 任务指向 `/zaza-preview`。这是独立数据作用域，不能据主站状态推断它已更新。

若确认扎扎也纳入本次 19.3 上线范围：先备份其配置与独立数据库，在隔离环境验收新 phase 的初次全量采集、空间关联、卡牌附魔与 Day 回放，再取得该范围的明确上线授权；同步 `ZAZA_PHASE/PHASE_START` 等显式参数并重启对应 Web 与 worker，恢复 Windows 采集后验证真实新增数据和最近成功同步时间。保留历史空间、订阅、审批设置、快照和停止空间的既有范围，不批量改旧标签或删库。

若未纳入本次范围，保留原配置并在完成记录中明确说明。不能因主站 `ENABLE_DAY_SNAPSHOT_QUEUE=0` 就盲目关闭扎扎独立采集，也不能为更新而开启普通 Day 队列；以各自原状态及本次授权为准。Windows 浏览器配置异常恢复、断点后的完整补查和失败重试逻辑必须保留，验收实际数据，不能只看计划任务 Running。

## 5. Git 收尾

在本地权威仓库运行测试，只暂存本次 SOP 相关文件，确认 `git diff --cached --name-status` 后提交。不要使用 `git add .`，服务器产生的备份、数据库、日志和发布素材都不能混入提交。

需要 push、PR 或合并时按本次用户授权执行；Git 操作不代替生产部署授权。工作区有其他功能改动时，分离本次补丁并对实际提交内容复验，不把整份脏文件混进提交。

## 6. 失败与回滚边界

| 场景 | 处理 |
| --- | --- |
| 预检缺文件、空间不足、schema 不支持、T0 不明或持续锁库 | 切换前停止；保留原服务和数据，先解决阻塞条件 |
| COS 上传/大小验证失败 | 不换库、不删除本地旧库备份；保留日志并核查对象 |
| 脚本中途失败 | 核对自动恢复结果；手动恢复额外备份的派生 JSON、卡图目录和受影响的外部配置，清理对应版本缓存并验收 |
| 脚本成功后才发现统计或资源错误 | 先暂停新写入并备份已采集的新 phase 数据；本地旧 runs 大副本可能已删除，需要从已记录的 COS 对象恢复，不能依赖自动 trap |
| 恢复旧 phase | 数据、phase 配置、GameData/翻译、派生资源、采集边界和相关缓存一起恢复，按原状态恢复服务与任务，避免新数据写进旧库 |

不要为补跑某个步骤直接重跑完整脚本：当前脚本没有拒绝相同 phase 的保护，即使目标已是 19.3 也可能再次归档并创建空库。先识别已完成步骤，单独修复资产、采集器或缓存。

自动 rollback 只覆盖脚本备份的文件，且恢复命令含容错处理；必须核查日志和服务/数据结果。完整性检查失败或服务恢复失败时不能把脚本退出或 `systemctl active` 当成业务已恢复。

## 7. 19.3 执行卡（2026-10-24 已上线并完成线上验收）

- [x] 用户确认实际发布时间：北京时间 `2026-10-24 08:00:00`，UTC `2026-10-24T00:00:00+00:00`。
- [x] 用户验收通过并明确授权主站 19.3 上线；扎扎不纳入本次切换。
- [x] 用户已上传新版文件；服务器暂存文件与同一本机运行时 cache 哈希一致，两个 SQLite 文件校验通过。
- [x] 重新读取 phase、资源哈希、schema、锁与挂载；准备源码哈希和派生资源备份。上线前系统盘约 13 GiB、备份盘约 17 GiB 可用。
- [x] 暂停实际 Windows 普通采集任务，更新真实副本 T0，保留严格过滤、Chrome 自动识别和认证恢复逻辑。
- [x] 完整归档 19.2，新建 19.3 空库、安装资产、重建资料并重启正式服务。
- [x] 验证 CDN、卡牌预览、分享资料 catalog 和缓存版本；首轮真实采集 484 条，早于 T0 的记录为 0。
- [x] 查询与统计线上验收通过，恢复原有定时任务；扎扎不变，路线和普通 Day 队列保持暂停。
- [x] 恢复公告 #16 保持草稿；归档和验收证据已记录。按 SOP 只提交本次变更，未经本次明确要求不 push 或合并。

10 月 21 日的回顾阶段只修改了本地 SOP；10 月 24 日在用户验收并明确授权后执行了下列生产更新。脚本的自动化缺口通过本次额外备份、采集暂停及单独资源安装补足，未将工作区其他功能一起发布。

### 2026-10-24 19.3 预检记录（上线前）

- 预检时生产为 `S19 / 19.2`，网站和机器人正常；只读统计为 108,779 条对局，最晚创建时间为北京时间 07:59:39，尚无可供当时预览使用的 08:00 后真实对局。
- GameData：`42414080` 字节，SHA-256 `f4fecd4d1de16d19bc8a987a1d8a8ebbde38293fc8d77faaed1801c22520cdd1`，含 3,375 张卡牌。
- 中文资源：`798720` 字节，SHA-256 `0e6141ccaf402f65f0bf2f093a84c734426e3e1f011b6b118d9a132b3c5bf3a3`，含 9,019 条翻译记录。
- 隔离重建得到 3,372 条卡牌映射、2,438 条名称翻译，未清理生产缓存。
- 两张物品、一张技能、各两种尺寸抽测：`z19.0` 均为 `200 image/webp`，`z19.3` 均为 404；候选继续沿用 `z19.0`。
- 普通采集实际副本已改用 `collector_browser.py` 自动识别 Chrome，当前浏览器为 156；仅在隔离候选中将 phase 起点改为本次 T0，保留既有认证、超时和网络恢复逻辑，未替换实际运行文件。
- 10 月 23 日上线的 WAL/连接释放、并发预算、冷启动重试、60 秒刷新合并和服务资源限制需要保持。线上健康 timer 和资源 drop-in 已只读确认仍生效；旧预览不重新启用。
- 基于 `a13c04c` 的隔离候选已通过 82 项测试，覆盖空库、边界、卡牌预览、分析更新、分享资源和查询保护；版本相关测试夹具已适配本次日期与 phase。真实 19.3 入库及采集恢复仍须在获准切换后验收。
- 准备目录：本地 `.tmp/season-19.3-20261024/`。普通采集边界候选为 `runtime-collector-candidate.py`；原实际采集器哈希为 `6a65b2f234bce48bca38baf768a2e8e89457d26687e2e1479fbfc1fcc5e9d086`，发布前重新比对，避免覆盖其他修改。
- 扎扎维持独立范围，不因本次主站候选自动切换；如需一起更新，另明确范围并验收。

### 2026-10-24 19.2 → 19.3 上线记录

- 用户明确授权：“验收通过，同意主站 19.3 上线”。上线前再次核对新资产与分享资料索引哈希，普通采集实际副本与预检哈希一致。
- 暂停普通计划任务并停止正式 Web、机器人，冻结时旧库共有 109,209 条对局。10:00 的既有采集发生在本次授权前，旧库完整归档，不批量改标签；新库重新按 19.3 创建时间边界采集。
- COS 归档：`history/bazaar_runs_s19_19.2_20261024_100809.db`，大小 `1222721536` 字节；SHA-256 `61814ddbe8387347c3dd98afa0ae0f45f343fa8c444b8eb34f13b3b2cdfac147`，对象大小复核一致。
- 脚本备份：`/opt/qiubot/backups/season_update_20261024_100809`；额外派生资源备份：`/opt/qiubot/backups/season_19.3_extra_20261024_100739/derived-assets-before.tar.gz`（`15770389` 字节）。备份目录仍保留数据盘兼容链接。
- GameData 与翻译安装后再次核对哈希一致；重建映射 3,372 条、名称翻译 2,438 条，清理 1 个旧单卡缓存。
- 分享资料索引保留 1,928 张卡牌的原有图片地址，刷新 7 条新版名称/尺寸/类型资料，未替换图片文件；索引 SHA-256 `93f0f08bf2a0a7277adb87632329cccce216da7d49dbb27ca7c90404de04112d`。卡图 CDN 沿用已验证的 `z19.0`。
- 当前参数：`CURRENT_SEASON_ID=RUNS_SEASON_ID=19`，`CURRENT_PHASE=19.3`，玩家查询仍为 20，`phases.start_time=2026-10-24T00:00:00+00:00`。数据库完整性为 `ok`，WAL 生效。
- Windows 普通采集成功匹配 Chrome 156，于 10:12:23 正常结束首轮：484 条入库，最早 UTC `2026-10-24T00:00:56.107+00:00`，最晚 UTC `2026-10-24T02:09:32.818+00:00`，早于 08:00 的记录为 0。任务恢复启用，退出码 0，下次北京时间 12:00。
- Redis `winrate_cache:19:19.3` 为 484 条；后台分析快照已刷新至 484 条，phase 正确、ready=true、error=null。真实物品 Chronobarrier 查询命中 5 条样本，小样本无组合时正常返回，未发生 500。
- 公网阵容列表、职业概况、阵容分析、卡牌预览、分析与列表页面均返回 200，预览 `dataLabel=19.3`。验收请求按既有限流间隔串行发送；初次过密请求触发一次预期的 429，调整检查间隔后通过，未修改限流规则。
- 正式应用及查询保护七个源码文件哈希保持不变；Web、机器人和资源健康 timer 正常，MemoryHigh/MemoryMax/TasksMax 保留原值。扎扎、路线下线状态、普通 Day 队列均未改变。
- 恢复公告 #16 为 draft，未发布。最终线上证据：`/opt/qiubot/backups/season_19.3_final_acceptance.json`（484 条）；中间验收快照：`/opt/qiubot/backups/season_19.3_public_acceptance.json`；运行日志：`/opt/qiubot/backups/season_update_19.3_20261024.log`。
- 隔离候选 82 项测试通过；本地本次配置、边界及 SOP 回归 27 项通过。数据库、卡图、日志、备份和敏感配置不入 Git。

参考已合并记录：[卡图身份修复 `530b92a`](https://github.com/CodeHuba/bazaarqiubot/commit/530b92a)、[阵容分析集成 `8d63813`](https://github.com/CodeHuba/bazaarqiubot/commit/8d63813)、[无效职业过滤 `14dda98`](https://github.com/CodeHuba/bazaarqiubot/commit/14dda98)、[案例选择与分享超时修复 `044f722`](https://github.com/CodeHuba/bazaarqiubot/commit/044f722)、[数据盘分工 `ae291ef`](https://github.com/CodeHuba/bazaarqiubot/commit/ae291ef)。阵容分析行为与缓存细节见[当前发布说明](https://github.com/CodeHuba/bazaarqiubot/blob/8436a3c/docs/comp-analysis.md)。

## 8. 19.4 执行卡（2026-10-29 主站与独立卡牌库已上线并验收）

- [x] 用户确认游戏于北京时间 `2026-10-29 00:00:00` 更新 19.4；UTC 边界为 `2026-10-28T16:00:00+00:00`，按创建时间包含边界。赛季保持 19，玩家查询保持 20。
- [x] 两个新版文件已在 `/home/ubuntu` 暂存，与本机同一运行时 cache 的哈希一致，SQLite quick_check 均为 ok。
- [x] 以最新远端 main `3e6f719f6a8ff52e81e48b76e3f4a756d74cac8e` 准备隔离候选，保留卡牌库、胜率图片、订阅及此前查询保护改动；不合入原工作区的其他修改。
- [x] 新 GameData 为 42,770,432 字节，SHA-256 `a55c3ceb36f0e02c72110e742243a9e72101d5bdc83d672b250fac4434c437d4`；中文资源为 806,912 字节，SHA-256 `2a2b37dddaa79d33cbb9d759851576d82de97d776ba663fc83b4e1ab7ac5927a`。
- [x] 隔离重建：3,386 张原生卡牌、3,383 条映射、2,449 条名称翻译，无损坏跳过；物品/技能中 7 张新增、7 张数据变化。实际 schema 的五张表均在归档工具支持范围内，空库演练通过。
- [x] 线上只读快照为 52,485 条 19.3 对局，其中 4,275 条创建于新边界之后；提取 800 条真实对局用于本地 19.4 验收，样本统计不代表完整新版统计，也未在生产改标签。
- [x] 识别独立卡牌库仍使用旧资产，健康接口为 19.3、fingerprint `f4fecd4d1de1`、1,850 张卡牌、38 位商人；同步纳入本次预览和待授权发布范围。
- [x] 实际普通采集器仍为 `D:\PJ\QiuBot\tools\bazaardb_runs_collector.py`。候选只改 phase 边界，保留 Chrome 识别、认证恢复及重试；原 SHA-256 `d331e06b5666b3ddfb97b7e5ad06bf8c1ae22235c92540a88c5a9dbb34095fb5`，发布前重查。首轮临时查询边界为 `Wed, 28 Oct 2026 16:00:00 GMT`，并安排合规旧库样本回填，避免遗漏零点以来数据。
- [x] 卡图抽测两张物品、一张技能的两个尺寸：`z19.0` 六次均为 200 image/webp，`z19.4` 六次均为 404，继续沿用 19.0。八张新增/ArtKey 变化的技能未取得可验证上游图片，隔离候选清空对应旧图并显示缺图占位；真实图片待后续核验，不能猜测地址或沿用旧身份图片。
- [x] 隔离回归：主站/赛季 152 项、机器人近期功能 52 项、图鉴 JavaScript 四组检查通过；预览实际 HTTP 检查 48 项、真实采集器候选时间边界 7 项通过。图鉴为 1,857 张卡牌、38 位商人、skipped=0，14 张新增/变化卡牌的详情及可用品质可读。阵容预演与机器人测试分进程执行，避免预演桩模块污染真实机器人导入。
- [x] 用户验收本地隔离预览并明确回复“可以上线”，授权主站及独立卡牌库 19.4 上线。
- [x] 记录服务与任务原状态，额外备份主站派生资源、独立卡牌库输入及配置；完整归档 19.3 并核对 COS 对象。
- [x] 创建 19.4 新库，安装同源资产、重建派生资料、同步独立卡牌库和实际采集器边界；不改变扎扎范围、路线维护、普通 Day 队列和原资源限制。
- [x] 回填、完整补采并确认无越界记录，主站、独立卡牌库与统计缓存复验通过；已恢复普通计划任务原启用状态，公告 #18 保持草稿。

准备目录：本机权威仓库 `.tmp/season-19.4-20261029/`，包含只读快照、资产、候选实际采集器和验收清单，全部不入 Git。预览入口为 `http://127.0.0.1:1054/`，提供真实样本阵容查询、独立卡牌库与空库演练。

### 2026-10-29 19.3 → 19.4 上线记录

- 用户在预览验收后明确同意“可以上线”。切换边界为北京时间当天 00:00，按创建时间筛选并包含边界。
- 完整归档 52,485 条旧库对局：`history/bazaar_runs_s19_19.3_20261029_170231.db`，590,147,584 字节，SHA-256 `494c2a6a7b75d2b32212921c792acb284799441650287dd8ed616a44d578d5aa`；COS 对象大小复核一致。
- 脚本备份为 `/opt/qiubot/backups/season_update_20261029_170231`，额外备份为 `/opt/qiubot/backups/season_19.4_extra_20261029_170226`。后者保存主站派生资料、独立图鉴输入/配置/受影响图片以及新边界回填数据，未改动既有发布目录链接。
- 旧库中 4,275 条创建时间合规记录已导出为 `backfill-created-after-cutoff.json.gz`，逐条校验 ID、时区、创建时间和胜场，再经当前 `/api/ingest` 分批全部回填为 19.4。最早 UTC `2026-10-28T16:00:01.404+00:00`，不批量改旧库标签，不清空 Windows 已采集 ID 库。
- 主站及独立图鉴安装同源新资产；主站派生映射/翻译重建通过，分享资料索引仅刷新一条名称元数据。独立图鉴通过 `LIBRARY_DATA_LABEL=19.4` 显式设置标签，健康接口为 1,857 张卡牌、38 位商人、fingerprint `a55c3ceb36f0`，无跳过卡牌。
- 发布中的主站索引属主遗漏已按原备份恢复为 UID/GID `1000:1001`、0600；主站卡牌预览随后返回 200、dataLabel=19.4。九项公网页面与 API 均通过，机器人和独立图鉴健康。
- 恢复公告 #18 保持 draft，未发布；证据 `/opt/qiubot/backups/season_19.4_recovery_draft.json`。公网中间验收 `/opt/qiubot/backups/season_19.4_public_acceptance.json`；发布指针 `/opt/qiubot/backups/season_19.4_release_pointer.json`；日志 `/home/ubuntu/season-19.4-release.log`。
- 普通采集器以临时 T0 查询范围和 600 页上限执行完整补采。首次在第 159 页因上游认证过期中断，已确认新增 556 条；依据已确认入库水位重开会话续采 93 页至新 phase 边界，续采新增 64 条，退出码 0。合计新增 620 条，与回填合计 4,895 条，早于零点记录为 0，数据库 integrity=ok、WAL 生效。临时环境仅限该进程，已恢复原计划任务默认四小时窗口，下次北京时间 2026-10-29 18:00:00。
- 最终 Redis 胜率缓存与后台分析 source_runs 均为 4,895 条，phase=19.4、ready=true、error=null；资源健康为 ok，既有资源限制、扎扎独立 19.1、路线维护与普通 Day 队列状态均保持。
- 新增及改动卡牌的主站/图鉴详情完成 28 项效果、属性、品质及附魔一致性检查，与隔离验收候选一致。最终线上证据：`/opt/qiubot/backups/season_19.4_final_acceptance.json`。


## 9. S20.0 准备卡（2026-11-04，仅准备，未切换）

用户告知预计北京时间 2026-11-05 上午当前时段更新 S20.0。本次对话时间约为 09:46；此为准备窗口参考，**不是已确认 T0**，不默认设为零点或 08:00。正式 T0、新版资产及隔离验收齐备，并取得此次上线授权后再执行切换。

### 已完成预检

- 准备分支 `codex/season-20-0-prep` 基于远端 main `5e43fbd`，含 S19.4 及 S 版本标签、胜率图片、订阅/监控和近期副英雄事件功能。复用已附加的隔离 worktree，不操作原脏工作区；实际执行日再次 fetch 和比对线上差异。
- 线上主站为 season=19、phase=19.4，在线库只含该范围；只读快照 42,096 条，最晚创建时间 UTC `2026-11-03T23:58:25.27+00:00`。这只是准备时的水位，正式归档前重新统计。
- Web、机器人、独立卡牌库均 active；系统盘约余 11 GiB，备份盘约余 17 GiB。Web 的 MemoryHigh/MemoryMax/TasksMax 仍为 `2411724800/2936012800/128`，正式窗口重新检查容量、锁和写入进程。
- 图鉴仍为 19.4、1,857 张卡牌、38 位商人、fingerprint `a55c3ceb36f0`。服务器暂存两个文件哈希均与上次 19.4 相同，**尚不是 S20.0 输入**；不能重用旧暂存文件执行大赛季更新。
- 确认主站翻译实际文件位于 `/opt/qiubot/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes`；不能只检查不存在的 `cache/zh-CN.bytes`。实际执行仍需检查显式环境配置与所有读取路径。
- Windows 普通任务仍运行 `D:\PJ\QiuBot\tools\run_collector.bat`，2026-11-04 08:00 最近一轮退出码 0；实际采集器 SHA-256 为 `97da6428518eea6c1cb753fce789c9eb50758826d24dc86cfeb83a35f16ff54d`，phase 边界仍为 19.4。未暂停任务、未修改运行配置。
- 赛季脚本 shell 语法校验通过；29 项赛季/归档/创建时间边界/玩家查询测试通过，包含新增跨大赛季空库用例。将只读提取的真实生产五表 schema 在临时库演练，目标 `(20, '20.0')`、phases=1、所有对局/快照/队列表为空、integrity=ok。对实际采集器函数以虚构时间运行 7 项边界校验通过；该夹具时间不作为真实 T0。
- 只读证据和演练输出存于权威仓库 `.tmp/season-20.0-20261104/`，不入 Git。扎扎仍为独立 19.1，主站路线维护为 1，普通 Day 队列为 0；本次未变更。
- QiuBot 游戏资料查询已明确纳入此次更新；确认实际服务入口 `/opt/qiubot/venv/bin/python main.py`、工作目录 `/opt/qiubot`。物品/技能、卡牌图片、野怪、商店/事件及副英雄事件相关 104 项回归通过；候选列表测试已隔离本机翻译缓存，避免因真实中文翻译存在而误报。此结果只验证当前代码，S20.0 新资产到位后仍须按第 0.5 节做新版资料验收。

### 更新日执行顺序

1. 确认真实 T0（北京时间与 UTC）、服务器新版同源 `GameData.db` 和 `zh-CN.bytes`；验证哈希、SQLite、卡牌与翻译关系，记录新增/改名/效果及职业归属变化。CDN、八张旧缺图技能和全部新卡重新核验，无法验证的图片使用占位。
2. 隔离重建映射、翻译、分享资料与独立卡牌库，按第 0.5 节同步准备 QiuBot 游戏资料查询资源，完成空库、真实新赛季样本预览及机器人查询渲染验收；核对近几次上线功能及 S20.0 标签。维护与恢复公告先拟稿，不承诺未经确认的恢复时刻。
3. 获准上线后暂停实际采集及其他写入者，额外备份派生资源、独立图鉴输入/配置和任务状态。原子写入保留原 UID/GID/权限，避免重现 19.4 卡牌预览读不到索引的问题。
4. 使用下列模板完整归档 S19.4、新建 S20.0 空库；T0 未确认前禁止执行。主站、机器人、独立图鉴和 Windows 采集边界分别处理，保持扎扎及路线/队列原状态。
5. 通过正常 ingest 回填合规记录，从 T0 完整补采；认证过期时记录已确认入库水位续采，不因恢复某一步再次运行完整换库脚本。核对新库只有 S20.0、早于 T0 或无效日期为 0、Redis/后台分析作用域正确且已就绪。
6. 完成主站与图鉴详情、QiuBot 游戏资料查询、图片/分享、玩家查询与近期功能验收，检查公网缓存及机器人实际加载资源，恢复原定时状态，记录归档对象/大小/哈希和最终水位；公告保留草稿供管理员发布。Git 提交和合并按当次授权执行。

```bash
# 仅供正式验收授权后使用；占位符必须替换为已确认的真实 T0。
cd /opt/qiubot
./tools/season_update.sh season 20 20.0 "<已确认的北京时间 YYYY-MM-DD HH:MM:SS>"
```

维护公告文案草稿：`S20.0 更新维护：主站及卡牌库将进行新赛季资料更新和数据治理，期间查询可能暂不可用。恢复进度将在完成验收后说明。`

恢复公告文案草稿：`S20.0 更新已完成，主站与卡牌库已恢复。当前统计仅包含 S20.0 对局，数据将随采集持续补充。`（只在实际验收完成后使用。）

## 10. S20.1 执行卡（2026-11-05，主站、QiuBot 与独立图鉴已上线验收）

本次实际目标为 `S20.1`，取代准备阶段预估的 `S20.0`；按大赛季切换，`season=20`、`phase='20.1'`、玩家查询参数按当前表达式为 21。用户确认真实 T0 为北京时间 `2026-11-05 03:00:00`，UTC `2026-11-04T19:00:00+00:00`；按创建时间包含边界。用户在隔离验收后明确回复“可以上线”，本次已据此完成生产切换。

- 用户已更新服务器暂存文件，与本机运行时 cache SHA-256 一致。GameData 45,170,688 字节，SHA-256 `06ce3a07b562fe52a2a8b21054db4208b5de49fc2592ef1cdbfa457feec77b39`；中文文件 823,296 字节，SHA-256 `dd92f992851d666b40ca7b2bcba3d20d21006a3d67afea358fd5aa495e3d96e9`；两个 SQLite quick_check 均为 ok。
- 隔离重建 3,442 条原生卡牌资料、3,439 条映射、2,494 条名称翻译，无损坏跳过。相较 S19.4，物品/技能新增 24 张、变化 435 张；459 张逐一检查详情、可用品质和机器人资料文档，无解析异常。178 个可查询野怪与 419 个商店/事件详情检查通过。
- 独立卡牌库候选 1,883 张卡牌、38 位商人、skipped=0，数据标签为 S20.1。初次简化采图脚本使用普通 HTTP 请求，被上游返回 403，导致 24 张新增卡牌显示占位；不能将此误判为上游没有图片。用户反馈耗尽装置缺图后，改用项目现有客户端取到精确原生 ID/类型对应的 `z20.0` 图片，大小两种图片均返回 200 image/webp；其余新增卡牌按相同流程复核，结果见准备目录 `image-checks.json` 与 `manifest.json`。此前 `z20.1` 抽测六次 404 只说明该资源版本地址不可用，不说明新卡无图；按接口返回的真实 URL 保存，不按 phase 拼接 CDN 版本。
- 生产只读快照仍为 47,944 条 S19.4 对局，其中提取到 705 条 T0 后创建的真实对局供隔离预览；最早 UTC `2026-11-04T19:04:45.93238+00:00`。只在本地按新范围构建样本，不改生产旧记录标签。正式切换时从旧库完整检查 T0 后记录并回填，不能以此快照替代全量补采。
- 当前生产五表 schema 已通过 `(20, '20.1')` 空库演练，phases=1，其余表全部为空；实际 Windows 采集器候选同时更新 SEASON_START/PHASE_START_UTC，7 项包含边界、时区和无效日期检查通过。原采集器 SHA-256 仍为 `97da6428518eea6c1cb753fce789c9eb50758826d24dc86cfeb83a35f16ff54d`，运行副本未修改。
- 机器人图片预演发现同一版本下缺图卡牌的渲染缓存文件名可能相同；候选在缓存指纹中加入原生 card ID，并增加两个缺图卡牌不共享图片路径的回归，待随此次验收发布。机器人/赛季相关 134 项测试通过。
- 图片复核最终完成：24 张新增卡牌均取得精确 ID/类型匹配的真实 `z20.0` 地址，大小两种图片共 48 次 200 image/webp；隔离资源和 QiuBot 样本图已更新，不再列为这 24 张新卡缺图。Web 相关另有 118 项回归通过。初次请求返回 403 的失败与 CDN 版本号不是 phase 的经验已纳入本次记录。
- 准备与证据目录为权威仓库 `.tmp/season-20.1-20261105/`，预览 `http://127.0.0.1:1056/`；提供新版卡牌库、705 条真实样本阵容查询、QiuBot 新卡查询图片样本。资源、数据库、日志和实际运行脚本候选均不入 Git。
- 用户验收后授权“可以上线”，完整归档 S19.4 共 47,944 条：`history/bazaar_runs_s19_19.4_20261105_095021.db`，539,942,912 字节，SHA-256 `2bbba59a9d444cd097721aa2266da33ac1ff822b25083967fe5c29ef51d478cd`，COS 对象大小复核通过。脚本备份 `/opt/qiubot/backups/season_update_20261105_095021`，额外资源及图鉴备份 `/opt/qiubot/backups/season_20.1_extra_20261105_095020`。
- 创建 `(20, '20.1')` 空库并同步同源资源、派生映射、翻译、24 张新卡图片与分享资料，主站、机器人和独立图鉴已重载；卡牌图片缓存指纹补入原生 ID 的最小修复已安装，原 UID/GID/权限保留。主站其他应用及查询保护源码哈希未变。
- 705 条创建时间合规旧库记录通过正常 ingest 回填，再以 T0 临时请求范围完整补采 60 页，新增 487 条，退出码 0，合计 1,192 条。早于 T0/无效日期为 0，integrity=ok、WAL 生效；Redis 与后台分析 source_runs 均追平，分析 ready=true、error=null。有效职业统计排除 2 条异常职业，1190 不是原始入库量。
- 实际 Windows 采集器只修改 SEASON_START 和 PHASE_START_UTC，其余逻辑 AST 不变；运行副本 SHA-256 `d6d0c728274ea7fae98e993be681efe65773484a5ebbdb7e176d10df0958f92f`。定时任务恢复 Enabled/Ready，下次北京时间 2026-11-05 10:00:00，默认四小时窗口；本轮临时 600 页与 T0 请求范围未写入计划任务。
- QiuBot 实际资源加载验收：1452 物品、582 技能、2494 名称翻译，玩家查询赛季参数为 21；耗尽装置真实卡图与资料可用，未发送 QQ 测试消息。独立图鉴 1883 张卡牌、38 位商人、skipped=0，指纹 06ce3a07b562。24 张新卡的主站/图鉴图片及默认品质详情完成 48 项检查；主站 v4 与图鉴 v3 原有可选品质过滤差异单独记录，未改变这两份解析器。
- 公网九项检查、服务、资源健康、资产哈希验收通过；扎扎继续独立 19.1，路线维护和普通 Day 队列状态不变。恢复公告已由管理员手动发布（用户确认），不重复发布。最终证据 `/opt/qiubot/backups/season_20.1_final_acceptance.json`，机器人与卡牌比对见同目录 `season_20.1_bot_acceptance.json`、`season_20.1_card_parity.json`；生产日志 `/home/ubuntu/season-20.1-release.log`。本地已同步实际赛季/边界；用户于 11 月 5 日最终验收通过并授权 Git 提交及远端合并。本次提交前机器人/赛季相关 134 项回归通过；已上线内容仅进行 Git 收尾，不再次部署。

正式命令模板（仅在本次验收通过并明确同意上线后执行）：

```bash
cd /opt/qiubot
./tools/season_update.sh season 20 20.1 "2026-11-05 03:00:00"
```

## 2026-10-14：19.1 → 19.2 执行记录

- 补丁切换时间：北京时间 `2026-10-14 08:00:00`，UTC `2026-10-14T00:00:00+00:00`；赛季编号保持 19。
- 上传文件通过 SQLite 校验，并与 Windows 运行时 cache 的 SHA-256 一致；安装后再次核对哈希一致。
- 归档旧库 83,591 条对局到 COS：`history/bazaar_runs_s19_19.1_20261014_092228.db`，大小 `941879296` 字节。
- 归档 SHA-256：`8fac7137166be7f9dd203d2b72425cf4a23563d865ed4ed3ae9bf8ea012ff5f6`。
- 更新前工具与缓存备份：`/opt/qiubot/backups/sop_preflight_20261014_092138`；脚本资产备份：`/opt/qiubot/backups/season_update_20261014_092228`。
- 新库从空库开始，完整性校验为 `ok`；重建 3,372 条卡牌映射和 2,438 条名称翻译，清理 92 个单卡旧缓存。
- 三张卡图分别实测 `z19.0` 返回 `200 image/webp`，`z19.2` 返回 404，沿用 `z19.0`。
- 使用 systemd 重启两个服务；内网与公网的 heroes、runs、hero_overview API 均返回 200。
- 实际采集计划任务仍运行 `D:\PJ\QiuBot\tools\bazaardb_runs_collector.py`。首次于 09:26 触发，发现请求边界不能阻止上游返回早期记录，已暂停并加上推送前创建时间校验；未启用已暂停的 Day 快照任务。
- 管理员明确确认：19.2 只保留北京时间当天 08:00 后创建的对局。于 09:32 备份首轮采集库到 COS `history/bazaar_runs_s19_19.2_before_creation_cutoff_20261014_093236.db`（`10530816` 字节），随后清理 695 条早期记录，保留 234 条。
- 清理后最早创建时间为 UTC `2026-10-14T00:00:39.4+00:00`，校验完整性为 `ok`；清理当前 phase 的 Redis 胜率缓存并重启两个服务，随后重新触发采集。
- 第二轮采集于 09:34 正常结束，新入库 30 条，共 264 条；早于 08:00 的记录为 0，Redis 胜率缓存与数据库同为 264 条。
- 网站维护公告 #11 已按管理员本次明确指令发布；恢复公告 #12 保持草稿，供管理员审核发布。
