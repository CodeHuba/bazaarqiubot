# BazaarQiuBot 赛季更新 SOP

本流程适用于 The Bazaar 同赛季补丁更新（如 `18.1 → 18.2`）和大赛季更新（如 `S18 → S19`）。线上目录固定为 `/opt/qiubot`，本地权威仓库为 `D:\PJ\QiuBot-github\QiuBot`。

## 更新原则

- 当前在线 runs 库只保存当前 `season/phase`，历史数据库完整归档到 COS。
- 不给旧 runs 批量改标签。每次 phase 切换都创建一份空的在线 runs 库。
- `GameData.db` 与 `zh-CN.bytes` 必须来自同一个运行时 cache 目录。
- 卡图 CDN 的 `z<version>` 不等于 phase。必须实测 CDN 后再改，不能把 `18.2` 自动当成 `z18.2`。
- 公告只创建草稿，由管理员审核后手动发布。
- Git 只从本地权威仓库提交，服务器工作树不是提交源。

## 1. 准备数据文件

权威来源：

```text
C:\Users\Administrator\AppData\LocalLow\Tempo Storm\The Bazaar\prod\cache\GameData.db
C:\Users\Administrator\AppData\LocalLow\Tempo Storm\The Bazaar\prod\cache\translations\zh-CN.bytes
```

上传到服务器暂存目录：

```bash
scp "/mnt/c/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/GameData.db" \
  ubuntu@101.34.58.217:/home/ubuntu/
scp "/mnt/c/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes" \
  ubuntu@101.34.58.217:/home/ubuntu/
```

确认两个 SQLite 文件可读、版本相互匹配。不要使用 Steam 安装目录中的同名 GameData。

## 2. 执行更新

同赛季补丁：

```bash
ssh -t ubuntu@101.34.58.217 \
  'cd /opt/qiubot && ./tools/season_update.sh patch 18.2 "2026-09-17 07:00:00"'
```

大赛季：

```bash
ssh -t ubuntu@101.34.58.217 \
  'cd /opt/qiubot && ./tools/season_update.sh season 19 19.1 "2026-10-01 10:00:00"'
```

只有明确复用已安装 GameData 和翻译时才使用 `--skip-assets`。

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

任何中途失败都会恢复应用文件和原在线数据库。

## 3. 卡图版本

先从现有 `card_images.json` 取一条图片 URL，用带浏览器 UA/Referer 的 GET 请求测试旧版和候选新版：

```bash
python3 - <<'PY'
import requests
urls = {
    "old": "https://s.bazaardb.gg/v1/z18.0/<hash>@256.webp?v=8",
    "candidate": "https://s.bazaardb.gg/v1/z18.2/<hash>@256.webp?v=8",
}
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://bazaardb.gg/"}
for name, url in urls.items():
    response = requests.get(url, headers=headers, timeout=15, stream=True)
    print(name, response.status_code, response.headers.get("content-type"))
PY
```

只有候选版本返回 `200 image/webp` 后，才通过 `CARD_IMAGE_VERSION` 或代码默认值切换并运行 `fetch_card_images.py`。如果候选返回 404，继续使用旧 CDN 版本。

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

## 5. Git 收尾

在本地权威仓库运行测试，只暂存本次 SOP 相关文件，确认 `git diff --cached --name-status` 后提交。不要使用 `git add .`，服务器产生的备份、数据库、日志和发布素材都不能混入提交。

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
