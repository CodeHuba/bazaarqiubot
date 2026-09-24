from pathlib import Path


WINRATE_HTML = Path(__file__).parents[1] / "web_runs" / "static" / "winrate.html"


def test_static_pages_display_official_hero_names_only():
    pages = list(WINRATE_HTML.parent.glob("*.html"))
    deprecated = (
        "海盗 ·", "工程师 ·", "法师 ·", "猪猪 ·", "机甲 ·", "吸血鬼 ·", "兽人 ·",
        "凡妮莎", "瓦妮莎", "瓦内萨", "斯黛拉", "斯黛儿", "斯特尔",
    )
    for page in pages:
        content = page.read_text(encoding="utf-8")
        assert not any(name in content for name in deprecated), page
    content = WINRATE_HTML.read_text(encoding="utf-8")
    for official in ("瓦内莎", "皮格马利翁", "斯黛尔", "双龙"):
        assert official in content


def test_hero_overview_share_has_button_podium_and_watermark():
    html = WINRATE_HTML.parent.joinpath("topcard.html").read_text(encoding="utf-8")
    assert html.count('id="overview-share-button"') == 1
    assert "async function shareOverview()" in html
    share_fn = html[html.index("async function shareOverview()"):html.index("function jumpToHero(")]
    assert "overview-share-podium" in share_fn
    assert "podiumColors" in share_fn
    assert "BazaarQiuBot" in share_fn
    assert "overview-days" in share_fn
    assert "overview-rank" in share_fn
    assert "sortBy" in share_fn
    assert "html2canvas(node" in share_fn
    assert 'download="bazaarqiubot-hero-overview.png"' in share_fn


def test_tier_share_only_includes_first_two_levels():
    html = WINRATE_HTML.parent.joinpath("topcard.html").read_text(encoding="utf-8")
    share_fn = html[html.index("async function preloadShareImages"):html.index("// ── 阵容榜 ──")]
    assert "(data.tiers||[]).slice(0,2)" in share_fn
    assert "const shareGroupsData=(data.tiers||[]).slice(0,2)" in share_fn
    assert "const shareCardCount=shareGroupsData.reduce" in share_fn
    assert "正在加载卡图 0/'+shareCardCount" in share_fn
    assert "正在生成分享图（布局中）" in share_fn
    assert "正在生成分享图（首次渲染）" in share_fn
    assert "正在使用兼容模式重新绘制" in share_fn
    assert "renderTierShareCanvas" in share_fn


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
