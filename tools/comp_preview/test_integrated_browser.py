"""Exercise the integrated website layout locally or on the public staging route."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.tmp/zaza-test-deps'))
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='http://127.0.0.1:1048/comp-preview')
    parser.add_argument('--output',default='integrated-local')
    parser.add_argument('--check-art',action='store_true')
    args=parser.parse_args()
    directory=ROOT/'.tmp/comp-integrated-staging'
    directory.mkdir(exist_ok=True)
    report={'base':args.base,'page_errors':[],'query_failures':[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
        page.on('response',lambda r:report['query_failures'].append({'url':r.url,'status':r.status})
                if '/api/comp/card' in r.url and r.status>=400 else None)
        url=args.base+'/runs?'+urlencode({'mode':'comp','cards':'灼烧梭鱼','auto':'1'})
        page.goto(url,wait_until='domcontentloaded')
        page.locator('.comp-analysis-group').first.wait_for(timeout=90000)
        assert page.locator('.nav-tab.active').inner_text()=='阵容查询'
        assert page.locator('.view-toggle-btn.active').inner_text()=='🔍 阵容分析'
        assert not page.locator('a[href="/routes"]').count()
        report['single_card_summary']=page.locator('.comp-summary').inner_text()
        report['single_card_groups']=page.locator('.comp-analysis-group').count()
        assert not page.locator('.comp-overall').count()
        for removed in ('查看全部查询样本的高频物品与技能','常见补充物品','常见技能','全组合样本 · 共同出现的技能'):
            assert removed not in page.locator('#comp-result').inner_text()
        first_group=page.locator('.comp-analysis-group').first
        assert first_group.get_by_role('heading',name='10 胜样本 · 物品',exact=True).is_visible()
        assert first_group.get_by_role('heading',name='10 胜样本 · 技能',exact=True).is_visible()
        assert first_group.locator(':scope > .comp-companions').count()==1
        report['winning_companions_visible_by_default']=True
        if args.check_art:
            response=page.request.get(args.base+'/api/comp/card?'+urlencode({'card':'灼烧梭鱼'}),timeout=45000)
            assert response.ok
            data=response.json()
            cards={}
            def collect(value):
                if isinstance(value,dict):
                    if value.get('cardId') and 'img' in value:cards[value['cardId']]=value
                    for child in value.values():collect(child)
                elif isinstance(value,list):
                    for child in value:collect(child)
            collect(data)
            missing=[c['name'] for c in cards.values() if not c['img']]
            assert not missing,missing
            page.locator('#comp-result details').evaluate_all('(details)=>details.forEach(detail=>{if(!detail.querySelector(":scope > summary")?.textContent.includes("截图"))detail.open=true})')
            page.locator('#comp-result .comp-card-img').evaluate_all('(images)=>images.forEach(image=>image.loading="eager")')
            page.wait_for_function('Array.from(document.querySelectorAll("#comp-result .comp-card-img")).every(image=>image.complete && image.naturalWidth>0)',timeout=45000)
            report['card_art']={'unique_cards_with_images':len(cards),'rendered_images_loaded':page.locator('#comp-result .comp-card-img').count(),'missing':missing}
        page.screenshot(path=str(directory/(args.output+'-desktop.png')))
        # Pick a second physical item from the site's real autocomplete.
        page.fill('#runs-card-search','船锚')
        page.locator('.runs-card-option').first.wait_for()
        page.locator('.runs-card-option').filter(has_text='船锚').first.click()
        assert page.locator('.runs-card-tag').count()==2
        page.evaluate('lastQuery=0')
        with page.expect_response(lambda response:'/api/comp/card?' in response.url) as pending:
            page.get_by_role('button',name='查询',exact=True).click()
        response=pending.value
        assert response.status==200
        data=response.json()
        assert len(data['anchors'])==2
        assert data['baseline']['count']>0
        for group in data['groups']:
            for card in group['winning_items']+group['winning_skills']:
                assert card['ten_wins']<=group['ten_wins']
                assert abs(card['ten_win_frequency']-card['ten_wins']/group['ten_wins'])<1e-12
        page.locator('.comp-analysis-group').first.wait_for()
        report['two_card_count']=data['baseline']['count']
        page.locator('.comp-analysis-group').first.locator(':scope > details > summary').last.click()
        assert page.locator('.comp-example').first.is_visible()
        assert page.locator('.comp-example .comp-instance').count()>0
        assert all('· 10 胜' in text for text in page.locator('.comp-example-head').all_text_contents())
        # Sorting uses another backend query, preserving all item filters.
        with page.expect_response(lambda response:'/api/comp/card?' in response.url) as pending:
            page.get_by_role('button',name='10 胜率',exact=True).click()
        sorted_data=pending.value.json()
        for level in ('regular','explore'):
            values=[g['rate'] for g in sorted_data['groups'] if g['sample_level']==level]
            assert values==sorted(values,reverse=True)
        page.wait_for_function('new URLSearchParams(location.search).get("sort")==="rate"')
        share=page.url
        page.reload(wait_until='domcontentloaded')
        page.locator('.comp-analysis-group').first.wait_for(timeout=90000)
        assert page.locator('.runs-card-tag').count()==2
        report['share_url']=share
        # Same page's list mode remains functional; no analysis filters leak into its contract.
        page.get_by_role('button',name='📋 阵容列表',exact=True).click()
        page.evaluate('lastQuery=0')
        with page.expect_response(lambda response:'/api/runs?' in response.url) as pending:
            page.get_by_role('button',name='查询',exact=True).click()
        list_data=pending.value.json()
        assert list_data['total']>0
        page.locator('.run-item').first.wait_for(timeout=15000)
        report['list_mode_count']=list_data['total']
        # Go back to the analysis permalink for mobile and the existing theme toggle.
        page.goto(share,wait_until='domcontentloaded')
        page.locator('.comp-analysis-group').first.wait_for(timeout=90000)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(directory/(args.output+'-mobile.png')))
        page.locator('.comp-analysis-group').first.scroll_into_view_if_needed()
        page.screenshot(path=str(directory/(args.output+'-mobile-result.png')))
        page.evaluate("localStorage.setItem('bazaarqiubot-theme','light')")
        page.reload(wait_until='domcontentloaded')
        page.locator('.comp-analysis-group').first.wait_for(timeout=90000)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(directory/(args.output+'-light.png')))
        report['mobile_and_light_theme']=True
        # Zero winning samples must show an honest empty state, never non-winning cards as a fallback.
        empty=json.loads(json.dumps(sorted_data))
        empty['groups']=empty['groups'][:1]
        empty['groups'][0].update(ten_wins=0,rate=0,winning_items=[],winning_skills=[])
        page.evaluate('data=>BazaarComp.render(data)',empty)
        assert '该组合暂无 10 胜样本' in page.locator('#comp-result').inner_text()
        assert not page.locator('.comp-analysis-group .comp-companions').count()
        assert not page.locator('.comp-example').count()
        report['no_winning_samples_empty_state']=True
        assert not report['page_errors'],report['page_errors']
        assert not report['query_failures'],report['query_failures']
        browser.close()
    (directory/(args.output+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
