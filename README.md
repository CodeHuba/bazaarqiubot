# 巴扎丘Bot · The Bazaar 中文社区数据平台

**bazaarqiubot.com** — 基于天梯真实对战数据的 The Bazaar 中文查询平台。

---

## 功能介绍

### 数据看板（`/`）

全局数据概览。展示总局数、英雄分布、每日采集量趋势等统计指标，快速了解当前赛季阵容库规模。

### 阵容查询（`/runs`）

查询天梯玩家的真实阵容记录：

- **筛选条件**：英雄、卡牌搜索多选（每张卡可指定附魔）、最近天数、段位；列表查询 10 胜记录，多张卡需同时满足
- **阵容列表**：展示卡牌及附魔、胜负场、玩家名、时间戳、截图；突出显示匹配卡牌，查询链接保留筛选条件和页码
- **阵容分析**：基于 FP-Growth 算法挖掘与关键卡牌共现的卡组体系，按 L1 体系 → L2 变种 → L3 细分 → 具体配置四层嵌套展示，附带截图和胜率
- **分享**：一键截图当前阵容分析结果

### 胜率对比（`/winrate`）

查询卡牌的 10 胜率，支持：

- 单卡或多卡组合（`火炮阵列 + 赛博铁尺`）
- 按英雄、时间范围筛选
- 多卡横向对比（≥3 张进入对比模式，展示领奖台排名）
- 结果分享截图

### 搭档分析（`/partner`）

分析某张卡牌在 10 胜阵容中的高频搭档，展示共现率和搭档胜率排名。

### 职业榜单（`/topcard`）

按职业维度展示：

- **卡牌胜率榜**：该职业使用率最高的卡牌及其 10 胜率排名
- **阵容体系榜**：FP-Growth 挖掘出的该职业主流阵容（四层嵌套结构）

### 冷知识（`/trivia`）

社区征集的 The Bazaar 游戏机制冷知识，均经过游戏原始数据验证，支持点赞投票。

### 赞助支持（`/support`）

项目赞助者名单与支持方式。

---

## 二次开发

### 环境要求

- Python >= 3.10
- Flask
- mlxtend（FP-Growth 阵容挖掘）
- python-dotenv

### 目录结构

```
QiuBot/
├── plugins/
│   └── bazaar_plugin/
│       ├── runs_query.py        # 阵容/胜率/搭档/FP-Growth 查询逻辑
│       ├── data_client.py       # 赛季/阶段常量
│       └── cache/
│           ├── GameData.db      # 游戏原始数据
│           ├── card_images.json # 卡牌图片 URL 映射
│           └── translations/    # 中文翻译
├── data/
│   └── bazaar_runs.db           # 玩家阵容数据库（SQLite）
└── web_runs/
    ├── app.py                   # Flask API 服务（端口 1027）
    └── static/                  # 前端页面（纯 HTML + CSS + JS）
        ├── index.html
        ├── runs.html
        ├── winrate.html
        ├── partner.html
        ├── topcard.html
        ├── trivia.html
        └── support.html
```

### 本地启动

**1. 克隆仓库**

```bash
git clone https://github.com/CodeHuba/QiuBot.git
cd QiuBot
```

**2. 创建虚拟环境并安装依赖**

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install flask mlxtend python-dotenv
```

**3. 准备数据文件**

将以下文件放到对应路径：

- `data/bazaar_runs.db` — 阵容数据库（SQLite，表结构见下）
- `plugins/bazaar_plugin/cache/GameData.db` — 游戏原始数据
- `plugins/bazaar_plugin/cache/card_images.json` — 卡牌图片映射

**4. 配置环境变量**

项目根目录创建 `.env`：

```
INGEST_TOKEN=your_token_here
```

**5. 启动**

```bash
cd web_runs
python app.py
```

访问 `http://localhost:1027`。

---

### 数据库结构

`bazaar_runs.db` 核心表 `runs`：

```sql
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    hero        TEXT,       -- Vanessa / Dooley / Mak / ...
    wins        INTEGER,
    losses      INTEGER,
    rank        TEXT,       -- legendary / diamond / gold / ...
    items       TEXT,       -- JSON 数组，卡牌名列表
    screenshot  TEXT,       -- 截图 URL（可为空）
    url         TEXT,       -- 原始战绩链接
    season      INTEGER,
    phase       TEXT,       -- 如 17.3
    created_at  TEXT        -- UTC 时间
);
```

### 写入数据

`POST /api/ingest`，Header 携带 `Authorization: Bearer <your_token>`，Body 为 JSON：

```json
{
  "runs": [
    {
      "username": "PlayerName",
      "hero": "Vanessa",
      "wins": 10,
      "losses": 3,
      "rank": "legendary",
      "items": ["火炮阵列", "赛博铁尺", "武装核心"],
      "screenshot": "https://...",
      "url": "https://bazaardb.gg/...",
      "season": 17,
      "phase": "17.3"
    }
  ]
}
```

---

### 生产部署

**启动/重启 Web 服务：**

```bash
# 启动
cd /opt/qiubot/web_runs
nohup venv/bin/python app.py > logs/web.log 2>&1 &

# 重启
kill $(pgrep -f 'venv/bin/python app.py') && sleep 1
cd /opt/qiubot/web_runs && { nohup venv/bin/python app.py > logs/web.log 2>&1 & }
```

**Caddy 反向代理（HTTPS）：**

```
bazaarqiubot.com, www.bazaarqiubot.com {
    reverse_proxy localhost:1027
}
```

---

## 相关链接

- [BazaarDB](https://bazaardb.gg/) — 数据来源
- [The Bazaar](https://www.howbazaar.gg/) — 官网
