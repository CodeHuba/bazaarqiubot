#!/usr/bin/env python3
"""
从 bazaardb.gg 批量拉取卡牌图片URL，存到 card_images.json
只拉取 Item 和 Skill 类型（bazaardb 只收录这两种）

反爬策略：
- 复用 bazaardb_client 的 curl_cffi + UA池 + TLS指纹伪装
- 1.5s~2.5s 随机间隔
- 每 100 张休息 30~60s
- 遇到 429/403 自动暂停 5 分钟
"""
import sys, json, os, time, sqlite3, random
import tempfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/opt/qiubot')
from plugins.bazaar_plugin import bazaardb_client as bdb
from plugins.bazaar_plugin import translations as trans
from plugins.bazaar_plugin.card_data_paths import get_gamedata_db_path

GAMEDATA_DB = get_gamedata_db_path(
    Path('/opt/qiubot/plugins/bazaar_plugin/cache/GameData.db')
)
OUTPUT_FILE = Path('/opt/qiubot/plugins/bazaar_plugin/cache/card_images.json')

# 只拉取这两种类型
VALID_TYPES = {'Item', 'Skill'}


def cache_needs_refresh(cached: dict, current_version: str) -> bool:
    """判断图片缓存是否为空或指向旧赛季资源。"""
    if not isinstance(cached, dict):
        return True
    expected = f"/v1/z{current_version}/"
    urls = [cached.get(key) for key in ("artLarge", "art")]
    # 两种尺寸只要存在，就都必须指向当前资源版本；缺失任一尺寸也需要刷新。
    return not urls or any(not isinstance(url, str) or expected not in url for url in urls)


# 当前 CDN 图片资源版本，可通过环境变量覆盖
IMAGE_VERSION = os.getenv("CARD_IMAGE_VERSION", "19.0")

def preserve_cached_record_on_failure(cached: dict | None, error: str = "") -> dict | None:
    """刷新失败时保留旧记录，调用方可另行记录 error。"""
    return cached


def image_urls_are_current(card_info: dict, current_version: str) -> bool:
    """确认接口返回的两种图片 URL 都存在且属于当前资源版本。"""
    if not isinstance(card_info, dict):
        return False
    expected = f"/v1/z{current_version}/"
    return all(
        isinstance(card_info.get(key), str) and expected in card_info[key]
        for key in ("Art", "ArtLarge")
    )


BATCH_SIZE = 100  # 每批休息一次
BATCH_REST = (30, 60)  # 每批后休息 30~60s
REQUEST_INTERVAL = (1.5, 2.5)  # 请求间隔
RETRY_WAIT = 300  # 遇到限流休息 5 分钟


def save_cache_atomic(output_file: Path, payload: dict) -> None:
    """以临时文件原子替换卡图缓存，避免中断留下半个 JSON。"""
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_file.name}.", suffix=".tmp", dir=output_file.parent
    )
    temp_file = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        temp_file.replace(output_file)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main():
    if GAMEDATA_DB is None or not GAMEDATA_DB.is_file():
        raise FileNotFoundError(
            "GameData.db 不存在，请设置 GAMEDATA_DB 或准备插件 cache/GameData.db"
        )
    conn = sqlite3.connect(str(GAMEDATA_DB))
    rows = conn.execute('SELECT Id, Data FROM cards').fetchall()
    conn.close()
    
    # 筛选出 Item 和 Skill
    targets = []
    for card_id, data_json in rows:
        try:
            data = json.loads(data_json)
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            print(f'跳过损坏的卡牌数据 {card_id}: {e}')
            continue
        if data.get('Type') in VALID_TYPES:
            targets.append((data['Id'], data))
    
    print(f'GameData.db 总卡牌: {len(rows)}')
    print(f'Item+Skill 数量: {len(targets)}')
    print(f'反爬策略: {REQUEST_INTERVAL[0]}~{REQUEST_INTERVAL[1]}s 间隔, 每 {BATCH_SIZE} 张休息 {BATCH_REST[0]}~{BATCH_REST[1]}s')
    
    # 加载已有数据
    result = {
        'meta': {
            'version': IMAGE_VERSION,
            'total': len(targets),
            'fetched': 0,
            'skipped': 0,
            'failed': 0,
            'last_update': datetime.utcnow().isoformat() + 'Z'
        },
        'cards': {}
    }
    
    if OUTPUT_FILE.exists():
        try:
            existing = json.loads(OUTPUT_FILE.read_text())
            cached_cards = existing.get('cards', {})
            result['cards'] = cached_cards if isinstance(cached_cards, dict) else {}
            result['meta']['skipped'] = 0
            print(f'已有缓存: {len(result["cards"])} 张，开始按版本检查\n')
        except Exception as e:
            print(f'加载缓存失败: {e}\n')
    
    # 遍历
    retry_count = 0
    for idx, (card_id, data) in enumerate(targets, 1):
        internal_name = data.get('InternalName', '')
        card_type = data.get('Type', '')
        art_key = data.get('ArtKey', '')
        
        # 当前版本缓存可以复用；旧版本或缺失图片需要重新拉取。
        cached_card = result['cards'].get(card_id)
        if cached_card is not None and not cache_needs_refresh(cached_card, IMAGE_VERSION):
            result['meta']['skipped'] += 1
            if idx % 100 == 0:
                print(f'[{idx}/{len(targets)}] 跳过当前版本缓存... (总计 {len(result["cards"])} 张)')
            continue

        print(f'[{idx}/{len(targets)}] {internal_name[:35]:35s} ... ', end='', flush=True)
        try:
            card_info = bdb.query_card_by_name(internal_name)
            
            # 检测限流
            if card_info is None:
                # 可能是真的没有，也可能被限流，简单判断
                if retry_count > 3:
                    print(f'⏸ 检测到可能的限流，休息 {RETRY_WAIT}s')
                    time.sleep(RETRY_WAIT)
                    retry_count = 0
                    # 重试
                    card_info = bdb.query_card_by_name(internal_name)
                else:
                    retry_count += 1
            else:
                retry_count = 0
            
            if card_info and image_urls_are_current(card_info, IMAGE_VERSION):
                zh_name = (card_info.get('Title') or {}).get('Text', '')
                if not zh_name or not trans.has_chinese(zh_name):
                    zh_name = trans.get_zh(internal_name) or internal_name
                
                result['cards'][card_id] = {
                    'id': card_id,
                    'internalName': internal_name,
                    'name': zh_name,
                    'type': card_type,
                    'art': card_info.get('Art', ''),
                    'artLarge': card_info.get('ArtLarge', ''),
                    'artBlur': card_info.get('ArtBlur', ''),
                    'artKey': art_key,
                    'uri': card_info.get('Uri', '')
                }
                result['meta']['fetched'] += 1
                print('✓')
            else:
                result['meta']['failed'] += 1
                print('✗ (无数据)')
                result['cards'][card_id] = preserve_cached_record_on_failure(cached_card)
        except Exception as e:
            result['meta']['failed'] += 1
            error_msg = str(e)[:50]
            print(f'✗ ({error_msg})')
            result['cards'][card_id] = preserve_cached_record_on_failure(cached_card, error_msg)
            
            # 检测 429/403
            if '429' in error_msg or '403' in error_msg or 'Forbidden' in error_msg:
                print(f'⚠️  检测到限流错误，休息 {RETRY_WAIT}s...')
                time.sleep(RETRY_WAIT)
        
        # 每 20 张保存一次
        if idx % 20 == 0:
            result['meta']['last_update'] = datetime.utcnow().isoformat() + 'Z'
            save_cache_atomic(OUTPUT_FILE, result)
            total_done = result['meta']['skipped'] + result['meta']['fetched']
            print(f'  → 已完成 {total_done}/{len(targets)} 张 (成功 {result["meta"]["fetched"]}, 失败 {result["meta"]["failed"]})')
        
        # 每批休息
        if idx % BATCH_SIZE == 0 and idx < len(targets):
            rest_time = random.uniform(*BATCH_REST)
            print(f'  💤 已完成 {idx} 张，休息 {rest_time:.1f}s...\n')
            time.sleep(rest_time)
        
        # 随机间隔（bazaardb_client 已经有 1.5s 基础限速，这里额外加一点）
        time.sleep(random.uniform(0, REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    
    # 最终保存
    result['meta']['last_update'] = datetime.utcnow().isoformat() + 'Z'
    save_cache_atomic(OUTPUT_FILE, result)
    
    print(f'\n✅ 完成！')
    print(f'  已有缓存: {result["meta"]["skipped"]}')
    print(f'  新拉取: {result["meta"]["fetched"]}')
    print(f'  失败: {result["meta"]["failed"]}')
    print(f'  总计: {len(result["cards"])} 张')
    print(f'  输出: {OUTPUT_FILE}')

if __name__ == '__main__':
    main()
