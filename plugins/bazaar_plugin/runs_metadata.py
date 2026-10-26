"""Versioned card metadata shared across queries; connections remain request-local."""
from collections import OrderedDict
from copy import deepcopy
import os
from pathlib import Path
import threading

FIELDS = ('card_mapping', 'name_to_ids', 'en_to_zh', 'zh_to_en', 'card_aliases',
          'hero_aliases', 'tex_map', 'size_map', 'card_heroes')
_cache = OrderedDict()
_lock = threading.Lock()


def file_version(path):
    path = Path(path).resolve()
    try:
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino
    except OSError:
        return str(path), None


def source_version(query, cache_dir, alias_path):
    from . import card_image_helper
    # Refresh the image dictionary before fingerprinting it. This also supports
    # explicitly supplied in-memory catalogs without mixing their metadata.
    images = card_image_helper._load(force=True)
    cache_dir = Path(cache_dir)
    candidates = (cache_dir/'GameData.db', cache_dir.parents[2]/'cache/GameData.db')
    configured = os.getenv('GAMEDATA_DB', '').strip()
    native = Path(configured).expanduser() if configured else next((p for p in candidates if p.is_file()), candidates[0])
    paths = (query.mapping_path, query.translations_path, native, str(native)+'-wal',
             card_image_helper._CACHE_FILE, cache_dir/'item_tex_map.json',
             '/opt/qiubot/data/card_heroes_map.json', alias_path)
    return tuple(file_version(p) for p in paths) + (id(images),)


def apply_cached(query, loader, cache_dir, alias_path):
    version = source_version(query, cache_dir, alias_path)
    key = (type(query)._load_metadata, version)
    with _lock:
        snapshot = _cache.get(key)
        if snapshot is None:
            for name in FIELDS:
                setattr(query, name, {})
            loader()
            snapshot = {name: getattr(query, name) for name in FIELDS}
            if version == source_version(query, cache_dir, alias_path):
                _cache[key] = snapshot
                while len(_cache) > 4:
                    _cache.popitem(last=False)
        else:
            _cache.move_to_end(key)
    # Older callers may mutate their own maps. Never let them alter the cache or
    # another thread's query; only immutable source snapshots are shared.
    for name, value in deepcopy(snapshot).items():
        setattr(query, name, value)
    query._metadata_version = version
