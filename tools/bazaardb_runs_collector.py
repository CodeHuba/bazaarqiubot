"""
BazaarDB Runs 数据采集脚本
使用 undetected-chromedriver 自动过 Cloudflare，拦截 /api/run 响应并翻页拉取数据。

使用方法：
1. pip install undetected-chromedriver selenium requests
2. python bazaardb_runs_collector.py

脚本会打开一个 Chrome 窗口，自动加载 bazaardb.gg/run，
然后通过模拟滚动/翻页拉取所有 runs 数据存入本地 SQLite。
"""

import json
import time
import sqlite3
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import urlencode, quote

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# === 配置 ===
INGEST_API = 'http://101.34.58.217:1027/api/ingest'
INGEST_TOKEN = 'eb3ff2d2de6ae942723c332e05882a8cfea2df1adf7d5b78'
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'bazaar_runs_ids.db')  # 本地只存已采集 ID
SEASON_START = 'Wed, 01 Jul 2026 16:32:42 GMT'  # 当前赛季开始时间
PHASE_START_UTC = '2026-10-14T00:00:00+00:00'  # 19.2，北京时间 2026-10-14 08:00
MAX_PAGES = 50  # 最多拉取页数（每页20条）
PAGE_DELAY_MIN = 3  # 每页最小间隔秒数
PAGE_DELAY_MAX = 6  # 每页最大间隔秒数（随机化，模拟人类）


def collection_created_after(now=None):
    """默认请求最近四小时，查询起点不早于当前 phase。"""
    override = os.environ.get('BAZAAR_RUNS_CREATED_AFTER')
    if override:
        return override
    current = now or datetime.now(timezone.utc)
    phase_start = datetime.fromisoformat(PHASE_START_UTC)
    return format_datetime(max(current - timedelta(hours=4), phase_start), usegmt=True)

def run_in_current_phase(run):
    """按已确认的创建时间口径校验，不能依赖上游查询参数过滤。"""
    created_at = run.get('createdAt')
    if not isinstance(created_at, str):
        return False
    try:
        created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    except ValueError:
        return False
    if created.tzinfo is None:
        return False
    return created >= datetime.fromisoformat(PHASE_START_UTC)


def init_db():
    """初始化本地 SQLite，只记录已采集的 run ID（防止重复推送）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        collected_at TEXT
    )''')
    conn.commit()
    return conn


def save_runs(conn, runs, min_wins: int = 1):
    """推送 runs 到服务器 API，本地只记录已采集 ID"""
    # 过滤低胜场
    filtered = [r for r in runs if (r.get('statWins') or 0) >= min_wins
                and run_in_current_phase(r)]
    if not filtered:
        return 0

    # 推送到服务器
    try:
        resp = requests.post(
            INGEST_API,
            json=filtered,
            headers={'X-Ingest-Token': INGEST_TOKEN},
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        new_count = result.get('new', 0)
        print(f"    → 服务器入库 {new_count} 条（总计 {result.get('total', '?')} 条）")
    except Exception as e:
        print(f"    ⚠️  推送服务器失败: {e}，跳过本页")
        return 0

    # 本地记录已采集 ID（用于判断是否重复）
    for run in filtered:
        try:
            conn.execute("INSERT OR IGNORE INTO runs (id, collected_at) VALUES (?, ?)",
                         (run['id'], datetime.now().isoformat()))
        except Exception:
            pass
    conn.commit()
    return new_count


def collect_runs():
    """主采集流程"""
    print("=" * 50)
    print("BazaarDB Runs 数据采集器")
    print("=" * 50)

    # 初始化数据库
    conn = init_db()
    existing = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"\n数据库已有 {existing} 条 runs")

    # 启动浏览器
    print("\n启动 Chrome（undetected 模式）...")
    options = uc.ChromeOptions()
    options.add_argument('--window-size=1200,800')
    # 启用 CDP 网络监听
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    driver = uc.Chrome(options=options, version_main=150)

    try:
        # 加载页面，等待 Cloudflare 验证通过
        print("加载 bazaardb.gg/run ...")
        driver.get("https://bazaardb.gg/run")

        print("等待页面加载完成（Cloudflare 验证）...")
        # 等待页面标题不是 Cloudflare 验证页
        for i in range(30):
            time.sleep(2)
            title = driver.title
            if 'Just a moment' not in title and 'Checking' not in title:
                break
            print(f"  等待 CF 验证... ({i*2}s)")
        else:
            if '--auto' not in sys.argv:
                print("❌ Cloudflare 验证超时，请手动完成验证后按 Enter")
                input()
            else:
                print("❌ Cloudflare 验证超时（自动模式，跳过）")
                driver.quit()
                return

        print(f"✅ 页面加载成功: {driver.title}")
        time.sleep(3)

        # 拦截网络请求，找到 t token
        print("\n提取认证 token...")
        t_token = extract_token_from_performance_log(driver)

        if not t_token:
            # 备选：从页面 JS 上下文提取
            print("  从 performance log 未找到，尝试从页面提取...")
            t_token = extract_token_from_page(driver)

        if not t_token:
            print("❌ 无法提取 t token，尝试直接用浏览器 fetch 拉取...")
            collect_via_browser_fetch(driver, conn)
            return

        print(f"✅ Token: {t_token[:40]}...")

        # 提取 cookies
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())

        # 开始翻页拉取
        print(f"\n开始拉取 runs 数据（最多 {MAX_PAGES} 页）...")
        collect_via_fetch_in_browser(driver, conn, t_token)

    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        new_count = total - existing
        print(f"\n{'=' * 50}")
        print(f"采集完成！数据库共 {total} 条 runs（本次新增 {new_count} 条）")
        conn.close()

        # 实时推送模式，无需 scp 上传
        print("✅ 数据已实时推送到服务器")

        print("按 Enter 关闭浏览器...")
        if '--auto' not in sys.argv:
            input()
        driver.quit()


def extract_token_from_performance_log(driver):
    """从 Chrome performance log 中提取 /api/run 请求的 t 参数"""
    try:
        logs = driver.get_log('performance')
        for entry in logs:
            msg = json.loads(entry['message'])['message']
            if msg['method'] == 'Network.requestWillBeSent':
                url = msg['params']['request']['url']
                if '/api/run' in url:
                    m = re.search(r't=(v2\.[^&]+)', url)
                    if m:
                        return m.group(1)
    except Exception as e:
        print(f"  performance log 提取失败: {e}")
    return None


def extract_token_from_page(driver):
    """尝试从页面上下文中找 token"""
    try:
        # 尝试从 Next.js 的 __NEXT_DATA__ 或 RSC payload 中提取
        result = driver.execute_script("""
            // 检查是否有全局变量存储了 token
            if (window.__NEXT_DATA__) {
                return JSON.stringify(window.__NEXT_DATA__);
            }
            return null;
        """)
        if result:
            m = re.search(r'v2\.\d+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+', result)
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def collect_via_fetch_in_browser(driver, conn, t_token):
    """在浏览器上下文中用 fetch 拉取数据（token/cookie 自动携带）"""
    total_new = 0
    cursor_created_at = None
    cursor_id = None

    for page in range(MAX_PAGES):
        # 构建 URL
        params = {
            'sort': 'newest',
            'order': 'desc',
            'createdAfter': collection_created_after(),
            't': t_token
        }
        if cursor_created_at and cursor_id:
            params['cursorCreatedAt'] = cursor_created_at
            params['cursorId'] = cursor_id

        url = '/api/run?' + urlencode(params, quote_via=quote)

        # 在浏览器中执行 fetch
        script = f"""
            const resp = await fetch('{url}');
            if (!resp.ok) return JSON.stringify({{error: resp.status}});
            const data = await resp.json();
            return JSON.stringify(data);
        """
        try:
            result = driver.execute_script(f"return (async () => {{ {script} }})()")
            data = json.loads(result)
        except Exception as e:
            print(f"  第 {page+1} 页 fetch 失败: {e}")
            # token 可能过期，尝试刷新
            print("  刷新页面重新获取 token...")
            driver.refresh()
            time.sleep(5)
            t_token = extract_token_from_performance_log(driver)
            if t_token:
                print(f"  ✅ 新 token: {t_token[:30]}...")
                continue
            else:
                print("  ❌ 无法刷新 token，停止采集")
                break

        if isinstance(data, dict) and 'error' in data:
            print(f"  第 {page+1} 页返回错误: {data['error']}")
            if data['error'] == 401:
                print("  Token 过期，刷新页面...")
                driver.refresh()
                time.sleep(5)
                t_token = extract_token_from_performance_log(driver) or t_token
                continue
            break

        if not data or not isinstance(data, list):
            print(f"  第 {page+1} 页无数据，采集结束")
            break

        # 保存数据
        new = save_runs(conn, data)
        total_new += new
        print(f"  第 {page+1} 页: {len(data)} 条 runs（新增 {new}）")

        if all(not run_in_current_phase(run) for run in data):
            print("  已到当前 phase 创建时间边界，采集结束")
            break


        # 遇到全重复页跳过，继续翻页（可能中间有漏采的）
        if new == 0:
            print("  本页全部为已有数据，继续翻页...")

        # 更新游标
        last = data[-1]
        cursor_created_at = last['createdAt']
        cursor_id = last['id']

        # 不足20条说明到底了
        if len(data) < 20:
            print("  已到最后一页")
            break

        time.sleep(PAGE_DELAY_MIN + (PAGE_DELAY_MAX - PAGE_DELAY_MIN) * __import__('random').random())

    print(f"\n本轮共新增 {total_new} 条 runs")


def collect_via_browser_fetch(driver, conn):
    """无 token 时，直接通过模拟页面滚动触发加载来收集数据"""
    print("监听网络请求，通过页面滚动触发加载...")

    # 先监听已有的请求
    total_new = 0

    for scroll in range(MAX_PAGES):
        # 滚动到底部
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)

        # 从 performance log 抓响应
        try:
            logs = driver.get_log('performance')
            for entry in logs:
                msg = json.loads(entry['message'])['message']
                if msg['method'] == 'Network.responseReceived':
                    url = msg['params']['response']['url']
                    if '/api/run' in url:
                        request_id = msg['params']['requestId']
                        # 获取响应体
                        try:
                            body = driver.execute_cdp_cmd(
                                'Network.getResponseBody',
                                {'requestId': request_id})
                            data = json.loads(body['body'])
                            if isinstance(data, list):
                                new = save_runs(conn, data)
                                total_new += new
                                print(f"  滚动 {scroll+1}: 捕获 {len(data)} 条（新增 {new}）")
                        except Exception:
                            pass
        except Exception as e:
            print(f"  滚动 {scroll+1} 监听失败: {e}")

    print(f"\n本轮共新增 {total_new} 条 runs")


if __name__ == '__main__':
    collect_runs()
