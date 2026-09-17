#!/usr/bin/env python3
"""Rebuild GameData-derived caches after installing matching runtime assets."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path

QIUBOT_ROOT = Path("/opt/qiubot")
DEFAULT_GAMEDATA = QIUBOT_ROOT / "plugins/bazaar_plugin/cache/GameData.db"
DEFAULT_TRANSLATIONS_DB = (
    QIUBOT_ROOT
    / "AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes"
)
DEFAULT_MAPPING = QIUBOT_ROOT / "data/card_id_mapping.json"
DEFAULT_TRANSLATIONS = QIUBOT_ROOT / "plugins/bazaar_plugin/cache/translations.json"
DEFAULT_CACHE_DIR = QIUBOT_ROOT / "plugins/bazaar_plugin/cache"
TYPE_MAP = {"TCardItem": "item", "TCardSkill": "skill"}


def _decode_card(raw: bytes | str) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    return json.loads(raw)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).replace(path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def rebuild_caches(
    gamedata: Path,
    translations_db: Path,
    mapping_path: Path,
    translations_path: Path,
    cache_dir: Path,
) -> dict[str, int]:
    with sqlite3.connect(f"file:{gamedata}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT Id, Data FROM cards").fetchall()
    with sqlite3.connect(f"file:{translations_db}?mode=ro", uri=True) as conn:
        localized = dict(conn.execute("SELECT hash, text FROM translation"))

    mapping: dict[str, dict[str, str]] = {}
    translated_names: dict[str, str] = {}
    skipped = 0
    for card_id, raw in rows:
        try:
            card = _decode_card(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            skipped += 1
            continue

        internal_name = card.get("InternalName") or ""
        if internal_name:
            mapping[card_id] = {
                "name": internal_name,
                "type": TYPE_MAP.get(card.get("$type", ""), "other"),
            }

        title = (card.get("Localization") or {}).get("Title") or {}
        title_en = title.get("Text") or ""
        title_key = title.get("Key") or ""
        title_zh = localized.get(title_key, "")
        if title_en and title_zh:
            translated_names[title_en] = title_zh

    _write_json_atomic(mapping_path, mapping)
    _write_json_atomic(translations_path, translated_names)

    removed = 0
    for path in cache_dir.glob("bazaardb_card_*.json"):
        path.unlink()
        removed += 1

    return {
        "cards": len(rows),
        "mapping": len(mapping),
        "translations": len(translated_names),
        "single_card_cache_removed": removed,
        "skipped_cards": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gamedata", type=Path, default=DEFAULT_GAMEDATA)
    parser.add_argument("--translations-db", type=Path, default=DEFAULT_TRANSLATIONS_DB)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()

    result = rebuild_caches(
        args.gamedata,
        args.translations_db,
        args.mapping,
        args.translations,
        args.cache_dir,
    )
    print(
        "GameData caches rebuilt: "
        f"cards={result['cards']} mapping={result['mapping']} "
        f"translations={result['translations']} "
        f"single_card_cache_removed={result['single_card_cache_removed']} "
        f"skipped_cards={result['skipped_cards']}"
    )


if __name__ == "__main__":
    main()
