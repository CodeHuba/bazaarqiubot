import pytest

from test_bz_db_command import load_plugin_module


@pytest.mark.parametrize(
    "previous,current,expected_wins",
    [
        pytest.param(1147, 1143, 9, id="anon02-2026-11-22-0705"),
        (950, 946, 8),
        (1080, 1076, 9),
        (1300, 1294, 8),
        (1147, 1146, 9),
        (1147, 1148, 10),
    ],
)
def test_history_infers_wins_from_score_change(monkeypatch, previous, current, expected_wins):
    load_plugin_module(monkeypatch)
    from plugins.bazaar_plugin.history_chart import parse_history

    history = parse_history([
        {"timestamp": "2026-11-22 06:35:08", "rating": previous, "position": 1},
        {"timestamp": "2026-11-22 07:05:08", "rating": current, "position": 1},
    ])
    game, = history[0]["games"]
    assert (game["delta"], game["wins"]) == (current - previous, expected_wins)
