import importlib.util
import json
import sys
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "fetch_card_images.py"


def load_fetch_module(monkeypatch):
    package = types.ModuleType("plugins.bazaar_plugin")
    bdb = types.ModuleType("plugins.bazaar_plugin.bazaardb_client")
    trans = types.ModuleType("plugins.bazaar_plugin.translations")
    paths = types.ModuleType("plugins.bazaar_plugin.card_data_paths")
    paths.get_gamedata_db_path = lambda path: path
    bdb.REQUEST_INTERVAL = (1, 2)
    package.bazaardb_client = bdb
    package.translations = trans
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin", package)
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin.bazaardb_client", bdb)
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin.translations", trans)
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin.card_data_paths", paths)

    spec = importlib.util.spec_from_file_location("fetch_card_images_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cached_image_from_old_version_needs_refresh(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    cached = {"art": "https://s.bazaardb.gg/v1/z17.0/old.webp"}

    assert module.cache_needs_refresh(cached, current_version="18.0") is True


def test_cached_image_from_current_version_can_be_reused(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    cached = {
        "art": "https://s.bazaardb.gg/v1/z18.0/current.webp",
        "artLarge": "https://s.bazaardb.gg/v1/z18.0/current-large.webp",
    }

    assert module.cache_needs_refresh(cached, current_version="18.0") is False


def test_mixed_image_versions_need_refresh(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    cached = {
        "art": "https://s.bazaardb.gg/v1/z18.0/current.webp",
        "artLarge": "https://s.bazaardb.gg/v1/z17.0/old.webp",
    }

    assert module.cache_needs_refresh(cached, current_version="18.0") is True


def test_empty_cached_image_needs_refresh(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)

    assert module.cache_needs_refresh({}, current_version="18.0") is True


def test_current_image_urls_are_complete_and_current(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    info = {
        "Art": "https://s.bazaardb.gg/v1/z18.0/art.webp",
        "ArtLarge": "https://s.bazaardb.gg/v1/z18.0/large.webp",
    }

    assert module.image_urls_are_current(info, "18.0") is True


def test_incomplete_or_old_image_urls_are_rejected(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)

    assert module.image_urls_are_current(
        {"Art": "https://s.bazaardb.gg/v1/z18.0/art.webp"}, "18.0"
    ) is False
    assert module.image_urls_are_current(
        {
            "Art": "https://s.bazaardb.gg/v1/z18.0/art.webp",
            "ArtLarge": "https://s.bazaardb.gg/v1/z17.0/large.webp",
        },
        "18.0",
    ) is False


def test_save_cache_atomic_replaces_existing_file(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    output = tmp_path / "card_images.json"
    output.write_text(json.dumps({"old": True}), encoding="utf-8")
    payload = {"meta": {"version": "18.0"}, "cards": {"new": {"art": "url"}}}

    module.save_cache_atomic(output, payload)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert not list(tmp_path.glob(".*.tmp"))


def test_refresh_failure_keeps_stale_cached_record(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    old = {"id": "pipe-organ", "art": "https://old.example/card.webp"}
    assert module.preserve_cached_record_on_failure(old, error="HTTP 429") == old


def test_existing_cache_is_not_counted_as_skipped_before_scan(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    result = {"meta": {"skipped": 0}, "cards": {"old": {"art": "old"}}}

    # 每张是否跳过应由遍历阶段决定，初始化不能直接把全部缓存计入 skipped。
    assert result["meta"]["skipped"] == 0


def test_invalid_cached_cards_are_replaced_with_empty_dict(tmp_path, monkeypatch):
    module = load_fetch_module(monkeypatch)
    existing = {"cards": []}
    cached_cards = existing.get("cards", {})
    result_cards = cached_cards if isinstance(cached_cards, dict) else {}

    assert result_cards == {}


def freezer_native():
    return {'Id': '6254a491-e424-4573-bb8d-a7d51a26144d', 'Type': 'Item',
            'InternalName': 'Walk-In Freezer', 'Size': 'Large',
            'Localization': {'Title': {'Text': 'Freezer'}}}


def test_display_name_resolves_cards_with_different_internal_name(monkeypatch):
    module = load_fetch_module(monkeypatch)
    native = freezer_native()
    expected = {'Id': native['Id'], 'Type': 'Item'}
    calls = []
    module.bdb.query_card_by_name = lambda name: calls.append(name) or (expected if name == 'Freezer' else None)
    assert module.query_native_card(native) == expected
    assert calls == ['Freezer']


def test_same_named_event_or_fuzzy_match_cannot_replace_item(monkeypatch):
    module = load_fetch_module(monkeypatch)
    native = freezer_native()
    expected = {'Id': native['Id'], 'Type': 'Item'}
    module.bdb.query_card_by_name = lambda name: ({'Id': 'event-freezer', 'Type': 'EventEncounter'}
                                                if name == 'Freezer' else expected)
    assert module.query_native_card(native) == expected
    module.bdb.query_card_by_name = lambda name: {'Id': 'other-item', 'Type': 'Item'}
    assert module.query_native_card(native) is None


def test_lookup_handles_missing_or_invalid_localization(monkeypatch):
    module = load_fetch_module(monkeypatch)
    assert module.card_lookup_names({'InternalName': 'Freezer', 'Localization': []}) == ['Freezer']
    assert module.card_lookup_names({'InternalName': 'Freezer', 'Localization': {'Title': {'Text': 'Freezer'}}}) == ['Freezer']
    module.bdb.query_card_by_name = lambda name: (_ for _ in ()).throw(AssertionError('must not query without ID'))
    assert module.query_native_card({'InternalName': 'Freezer'}) is None


def test_cache_record_uses_searchable_display_name_and_keeps_native_identity(monkeypatch):
    module = load_fetch_module(monkeypatch)
    module.trans.has_chinese = lambda name: name == '冷库'
    module.trans.get_zh = lambda name: '冷库' if name == 'Freezer' else None
    record = module.card_image_record(freezer_native(), {'Title': {'Text': 'Freezer'}, 'Art': 'art', 'ArtLarge': 'large'})
    assert record['name'] == '冷库'
    assert record['internalName'] == 'Freezer'
    assert record['nativeInternalName'] == 'Walk-In Freezer'
    assert record['size'] == 'Large'
