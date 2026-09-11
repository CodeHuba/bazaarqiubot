from pathlib import Path


WINRATE_HTML = Path(__file__).parents[1] / "web_runs" / "static" / "winrate.html"


def _share_function():
    html = WINRATE_HTML.read_text(encoding="utf-8")
    return html[html.index("async function shareWinrate()"):html.index("async function queryPartner()")]


def test_winrate_share_card_has_one_dom_instance_and_waits_for_card_images():
    html = WINRATE_HTML.read_text(encoding="utf-8")
    assert html.count('id="wr-share-card"') == 1
    assert html.count('id="wr-share-card-wrap"') == 1
    assert "await preloadWinrateShareImages(card)" in _share_function()


def test_winrate_share_images_use_same_origin_proxy_for_canvas():
    share_fn = _share_function()
    assert "api/card_art_proxy?url=" in share_fn
    assert "encodeURIComponent(c.img)" in share_fn
    assert 'data-src="${shareImageUrl}"' in share_fn
    assert 'src=""' in share_fn
