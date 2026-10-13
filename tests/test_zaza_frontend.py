import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "web_runs" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_public_zaza_has_real_multispace_flow_and_canonical_profile_contract():
    html = read("zaza.html")
    js = read("zaza.js")
    assert 'https://bazaardb.gg/run/profile/UUID' in html
    assert "/api/zaza/spaces?query=" in js
    assert "profileUrl,name" in js
    assert "location.hash = data.space.id" in js
    assert "endsWith('空间')" in js
    assert "encodeURIComponent(id)" in js
    assert "space.id" in js and "改名后链接仍有效" in js


def test_public_zaza_supports_summary_progress_filters_cards_and_pagination():
    html, js = read("zaza.html"), read("zaza.js")
    for marker in ("metrics", "hero-distribution", "recent-days", "card-search", "previous", "next"):
        assert f'id="{marker}"' in html
    assert "summary.average_wins" in js
    assert "ten_win_rate" in js
    assert 'id="progress"' not in html
    assert "query.set('cards'" in js
    assert "perPage:5" in js
    assert "query.set('start'" in js
    assert "pagination.total_pages" in js
    assert "object-fit:contain" in read("zaza.css")
    assert "quality" not in js.lower() and "enchantment" not in js.lower()
    assert "即将推出" in html


def test_public_zaza_card_search_is_race_safe_and_uses_existing_api():
    js = read("zaza.js")
    assert "/api/card_search?q=" in js
    assert "suggestionRequest" in js
    assert "state.cards.some" in js
    assert "card.cardId" in js
    assert "同时包含全部所选卡牌" in read("zaza.html")


def test_admin_page_has_protected_api_actions_and_settings():
    html, js = read("zaza-admin.html"), read("zaza-admin.js")
    assert "/api/admin/zaza/spaces" in js
    assert "/api/admin/zaza/settings" in js
    for action in ("approve", "reject", "rename", "retry", "cancel", "stop", "resume", "delete"):
        assert action in js
    assert "review_mode" in js
    assert "prompt(" in js and "confirm(" in js


def test_zaza_javascript_syntax(tmp_path):
    for name in ("zaza.js", "zaza-admin.js"):
        result = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_feedback_links_native_zaza_admin_entry():
    feedback = read("feedback.html")
    assert 'href="/admin/zaza"' in feedback
    assert "空间管理" in feedback
