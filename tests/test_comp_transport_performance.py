from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web_runs" / "app.py"


def _route(source: str, name: str, next_route: str) -> str:
    start = source.index(f"def {name}(")
    end = source.index(next_route, start)
    return source[start:end]


def test_comp_card_uses_prebuilt_card_search_index_instead_of_reading_json_per_request():
    source = APP.read_text(encoding="utf-8")
    route = _route(source, "api_comp_card", "\n# ===== Feedback API =====")

    assert "open('/opt/qiubot/plugins/bazaar_plugin/cache/card_images.json'" not in route
    assert "_find_card_search_matches" in route
    assert "query.find_card_ids(card_raw)" in route
    assert "for candidate_id in candidate_ids" in route


def test_large_read_only_json_apis_get_short_public_cache_headers():
    source = APP.read_text(encoding="utf-8")
    after = source[source.index("def after_request(response):"):source.index("\ndef _log_api_call", source.index("def after_request(response):"))]

    assert "'/api/comp'" in after
    assert "'/api/comp/card'" in after
    assert "response.cache_control.max_age = 60" in after
    assert "response.cache_control.public = True" in after


def test_caddy_config_enables_json_compression():
    config = (ROOT / "deploy" / "caddy" / "bazaarqiubot-encode.caddy").read_text(encoding="utf-8")

    assert "encode zstd gzip" in config
