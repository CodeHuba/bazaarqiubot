from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS_HTML = ROOT / "web_runs" / "static" / "runs.html"


def _html() -> str:
    return RUNS_HTML.read_text(encoding="utf-8")


def test_runs_card_filter_uses_multiselect_tag_input_and_hidden_protocol_value():
    html = _html()

    assert 'id="runs-card-picker"' in html
    assert 'id="runs-card-tags"' in html
    assert 'id="runs-card-search"' in html
    assert 'id="runs-card-suggestions"' in html
    assert 'id="runs-cards" type="hidden"' in html
    assert "selectedRunCards.join('+')" in html


def test_runs_card_picker_fetches_debounced_candidates_and_supports_keyboard_selection():
    html = _html()

    assert "/api/card_search?q=" in html
    assert "clearTimeout(cardSearchTimer)" in html
    assert "cardSearchController.abort()" in html
    assert "event.key === 'ArrowDown'" in html
    assert "event.key === 'ArrowUp'" in html
    assert "event.key === 'Enter'" in html
    assert "event.key === 'Escape'" in html
    assert "event.key === 'Backspace'" in html


def test_runs_card_picker_restores_legacy_plus_separated_links_as_tags():
    html = _html()

    assert "restoreRunCards(cards)" in html
    assert ".split('+')" in html
    assert "addRunCard" in html


def test_runs_comp_analysis_requires_exactly_one_selected_core_card():
    html = _html()

    assert "selectedRunCards.length !== 1" in html
    assert "阵容分析一次只能选择一张核心卡" in html
    assert "const card = selectedRunCards[0]" in html


def test_selecting_candidate_does_not_submit_query_automatically():
    html = _html()
    start = html.index("function selectRunCardSuggestion")
    end = html.index("\n}", start) + 2
    function = html[start:end]

    assert "queryRuns" not in function
    assert "if (e.defaultPrevented) return" in html


def test_popular_card_suggestion_adds_a_run_card_tag_instead_of_overwriting_hidden_value():
    html = _html()
    start = html.index("function applySuggestion")
    end = html.index("\n}", start) + 2
    function = html[start:end]

    assert "if (isRuns) addRunCard(val)" in function


def test_closing_or_replacing_search_invalidates_inflight_candidate_response():
    html = _html()

    assert "let cardSearchRequestId = 0" in html
    assert "cardSearchController.abort()" in html
    assert "const requestId = ++cardSearchRequestId" in html
    assert "requestId !== cardSearchRequestId" in html
    assert "input.value.trim() !== query" in html


def test_global_enter_handler_ignores_interactive_controls():
    html = _html()

    assert "e.target.closest('button, a, select')" in html


def test_mobile_card_picker_can_shrink_inside_narrow_form():
    html = _html()

    assert "@media(max-width:700px)" in html
    assert ".runs-card-picker{min-width:0;max-width:100%;}" in html.replace(" ", "")


def test_legacy_raw_plus_cards_query_is_restored_without_plus_becoming_space():
    html = _html()

    assert "readLegacyCardsParam(location.search)" in html
    assert "raw.replace(/\\+/g, '%2B')" in html


def test_runs_filters_hide_win_and_game_day_controls_but_keep_fixed_ten_win_query():
    html = _html()

    assert 'id="runs-wins"' not in html
    assert 'id="runs-day"' not in html
    assert '最低胜场' not in html
    assert '游戏 Day' not in html
    assert "const wins = '10'" in html
    assert "min_wins: wins" in html
    assert "params.set('day'" not in html
    assert "最近天数" in html
    assert "不限（全部时间）" in html
    assert "填写 N 表示只看最近 N 天" in html
    assert "{hero, cards, wins, days, day}" not in html


def test_card_picker_stays_single_height_and_form_grid_prevents_rank_button_overlap():
    compact = _html().replace(" ", "")

    assert ".runs-card-picker-box{height:40px;min-height:40px" in compact
    assert "flex-wrap:nowrap" in compact
    assert "overflow-x:auto" in compact
    assert "grid-template-columns:minmax(130px,.8fr)minmax(280px,2fr)minmax(90px,.65fr)minmax(110px,.75fr)minmax(82px,auto)" in compact
    assert "@media(max-width:900px)" in compact
