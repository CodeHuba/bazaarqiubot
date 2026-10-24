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
- 列出当前 runs 库 schema，与 `season_archive_runs.py` 的允许表集合比对，并在临时目标库演练。新增表必须先分类处理，不能通过删表绕过校验。
- 除脚本自带备份外，提前备份 `card_id_mapping.json`、`translations.json`、`card_images.json`、待变更的卡图/附魔等本地资源索引、`bazaardb_card_*.json`、`web_runs/static/comp-art/catalog.json` 及其关联图片；记录环境和计划任务状态，敏感配置只保存在服务器受限备份中。
- 在隔离环境验收新资料、空库和少量真实新版样本；日期相关测试要随已确认 T0 调整。取得明确生产更新授权后才进入第 2 节。

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
- 恢复公告 #16 为 draft，未发布。线上证据：`/opt/qiubot/backups/season_19.3_public_acceptance.json`；运行日志：`/opt/qiubot/backups/season_update_19.3_20261024.log`。
- 隔离候选 82 项测试通过；本地本次配置、边界及 SOP 回归 27 项通过。数据库、卡图、日志、备份和敏感配置不入 Git。

参考已合并记录：[卡图身份修复 `530b92a`](https://github.com/CodeHuba/bazaarqiubot/commit/530b92a)、[阵容分析集成 `8d63813`](https://github.com/CodeHuba/bazaarqiubot/commit/8d63813)、[无效职业过滤 `14dda98`](https://github.com/CodeHuba/bazaarqiubot/commit/14dda98)、[案例选择与分享超时修复 `044f722`](https://github.com/CodeHuba/bazaarqiubot/commit/044f722)、[数据盘分工 `ae291ef`](https://github.com/CodeHuba/bazaarqiubot/commit/ae291ef)。阵容分析行为与缓存细节见[当前发布说明](https://github.com/CodeHuba/bazaarqiubot/blob/8436a3c/docs/comp-analysis.md)。

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
