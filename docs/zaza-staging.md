# Zaza 隔离 staging

## 当前验收记录（2026-10-13）

已在现有 staging 服务完成创建、对局同步、统计和逐日快照验收。19.1 阶段起点由用户确认是北京时间 2026-10-08 09:00，即 `ZAZA_PHASE_START=2026-10-08T01:00:00Z`。API 展示和 worker 成员同步均使用该时间窗口；早于起点的种子记录保留在隔离库，但不计入当前空间。

- Windows 专用采集任务恢复运行，注册脚本记录日志，并配置失败后每分钟重启、最多三次。
- 修正原生胜负字段映射、越过阶段起点后继续翻旧页的行为，以及 RSC 长文本块解析。
- 验收时茶茶空间 29 局、29 局快照；丘丘空间 12 局、12 局快照，平均胜场 9.17、10 胜率 75%；Puffy 当前阶段成功同步为 0 局，旧 6 局保留。
- 后端、解析器、接口集成和前端静态检查共 97 项测试通过；真实公开 API 与采集完成记录已核对。
- 修复前的代码和 SQLite 数据备份保存在服务器相应 staging 目录。认证令牌、Chrome 用户目录、运行日志和本地验收数据不纳入 Git。

以下为早期安装预检记录，不能用于判断当前部署和暂停状态。

候选文件已完成本地启动及行为验证；本轮未执行 `--apply`，未修改生产服务、数据或 Caddy。

## 执行

```bash
cd /mnt/d/PJ/QiuBot-github/QiuBot
python3 tools/deploy_zaza_staging.py         # SSH 只读预检
python3 tools/deploy_zaza_staging.py --apply # 父代理执行的已授权 staging 安装
```

脚本固定目标 `ubuntu@101.34.58.217`，安装 `/opt/qiubot-staging/zaza/app` 与两个服务 `qiubot-zaza-staging`、`qiubot-zaza-staging-worker`。无需手工准备密码文件：root 从现有生产 `.env` 读取 STATS_PASSWORD，写入 `/etc/qiubot-zaza-staging.env`（0600），不打印密码。传输 bundle 校验 SHA256。现有 staging 路径、env 或 unit 会导致拒绝覆盖。启动失败时停止、移除新建 unit，保留隔离文件供诊断；不会修改生产目录。

## 数据和 worker

生产路径和赛季通过解析生产 app/data_client 的 AST 与 `.env` 获取，不导入主应用或插件包。实际只读预检得到 season=19、phase=19.1，runs=/opt/qiubot/data/bazaar_runs.db（228499456 bytes），snapshots=/opt/qiubot/data/bazaar_day_snapshots.db（105205760 bytes），磁盘空闲约 12.4 GB。生产 `.env` 当前 ENABLE_DAY_SNAPSHOT_QUEUE=0。

种子仅复制由 username=qqr 查到的真实 profile（0b180fb3-7fb9-430d-85c8-019b3658d8c7）当前范围的 runs 及匹配 combat_snapshots，分别使用只读事务保持各库内部一致性。两个源库没有跨库原子时间点保证。保留原表 schema；不复制整库、历史全局任务或全局队列。seed.json 记录 profile、范围与实际行数。这个有限种子只适合 qqr 验收；其他个人页可以申请和验证昵称，但不会拥有未提取的生产 runs。

runs 和卡图/翻译缓存只读。独立 `/var/lib/qiubot-zaza-staging/spaces.db` 保存申请、审核、设置、审计、outbox；`snapshots.db` 是匹配历史事实的可写隔离副本，后续采集写这里。服务的 ProtectSystem=strict、ReadOnlyPaths=/opt/qiubot 与 ReadWritePaths 限定写入 staging state。

harness 的 `--worker` 调用既有 `tools.zaza_space_worker.process_once`，复用 tracker_rsc 与 day_snapshot_store，避免原 worker CLI 的插件初始化导入。worker 每次检查只读生产 `.env` 的暂停开关；缺失/不可读时暂停。生产仍暂停时批准申请保持 creating，恢复后继续执行；这是实际暂停行为，不是缺少 worker。不要为验收修改生产暂停开关。

CardResolver 使用标准库读取复制的 card_images.json/translations.json（支持有真实 GameData 时优先 native ArtLarge/Art），提供 callable、技能展示与中英文子串搜索。不构造 CDN 地址，不写缓存，不同步回源。部署 bundle 不依赖插件 `__init__` 或 ncatbot。

## 路由

loopback 为 `127.0.0.1:1030`。harness 支持完整 `/zaza-preview/` 请求与 Caddy handle_path 已剥离前缀的请求。HTML/JS 只重写字符串边界上的 `/static`、`/api/zaza`、`/api/admin/zaza`、`/api/card_search`、`/admin/zaza`、`/zaza`；其他生产导航与 API 不被接管。分享链接也使用前缀。

父代理完成部署验收后可在现有站点添加以下精确候选路由（本脚本不改 Caddy）：

```caddy
handle_path /zaza-preview/* {
    reverse_proxy 127.0.0.1:1030
}
```

公开页 `/zaza-preview/zaza`；管理页 `/zaza-preview/admin/zaza`；健康 `/zaza-preview/healthz`。管理页及 API 检查 Basic Auth。settings 直接使用 blueprint 的持久化接口，不伪造响应。

## 实际验收与限制

本地临时隔离 SQLite 和真实 `cache/GameData.db`（3327 张卡牌）验证：health、管理鉴权、settings PUT/GET、重新创建 app 后读回、前缀 HTML/JS 与排除非 Zaza URL、子串搜索、申请 pending、审核 creating、暂停 worker 无状态变化、实际 BazaarDB profile 请求返回 qqr 后 worker 转为 active。输入 runs SHA256 未变。独立启动 `python3 tools/zaza_staging_app.py` 后真实 HTTP health 返回 `{ok:true,season:19,phase:"19.1"}`。

验证脚本位于 `/tmp/verify_zaza_staging_local.py`，验收临时目录 `/tmp/zaza-local-04_r_7xk`；本地 runs 只有 schema，没有伪造数据。通过 SSH 导出生产真实 seed 的操作被自动审批阻断，没有重试或绕过，因此完整 seed 安装与基于其卡图缓存的 CDN 验收尚未执行。已执行部署脚本 dry-run 和嵌入安装代码编译检查。

真实 tracker `5b41a858-c02f-40b1-979c-378c10ee4219` 请求 HTTP 200，现有解析器得到 0 条 CombatStart；存储层抛出 `combat snapshots contain no valid hour6 PvP entries`，没有写入成功事实。不能宣称该 live capture 已通过；父代理应使用 qqr 已提取 runs 验收并检查真实 tracker 响应/登录要求。

停止 staging：`sudo systemctl disable --now qiubot-zaza-staging qiubot-zaza-staging-worker`。只删除明确 staging unit/env/目录即可撤销；生产不属于回滚范围。
