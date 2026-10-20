import copy

from test_runs_list_performance import runs_query
from plugins.bazaar_plugin.comp_analysis import Analysis
from plugins.bazaar_plugin.comp_examples import one_item_apart, select_examples


def rows(items, count, prefix='run', hero='Vanessa', date='2026-10-19T00:00:00Z', wins=10):
    return [dict(id=f'{prefix}-{i}', hero=hero, wins=wins, created_at=date,
                 items=[dict(cardId=cid, tier='Gold', slotPosition=j) for j, cid in enumerate(items)],
                 skills=[dict(cardId='skill', tier='Silver')], screenshot=f'/{prefix}-{i}.png') for i in range(count)]


def catalog(*items):
    return {cid: dict(name=cid, type='Item', heroes=['Vanessa']) for cid in items}


def test_frequency_beats_home_ownership_and_recency():
    cards = catalog('q','a','b','c','d','e','f','g','h','i')
    cards['a']['heroes'] = ['Dooley']
    records = rows(['q','a','b'],10,'frequent') + rows(['q','d','e'],6,'native') + rows(['q','g','h'],1,'newest',date='2026-10-20T00:00:00Z')
    selected = select_examples(records,cards)
    assert [row['id'].split('-')[0] for row,_,_ in selected] == ['frequent','native','newest']
    assert [count for _,count,_ in selected] == [10,6,1]


def test_same_frequency_prefers_native_and_neutral_not_unknown():
    cards = catalog('q','a','b','c','d','e','f','g','h')
    cards['a']['heroes'] = ['Dooley']; cards['d']['heroes'] = ['Common']
    cards['g'].pop('heroes'); cards['h'].pop('heroes')
    records = rows(['q','a','b'],4,'foreign') + rows(['q','d','e'],4,'neutral') + rows(['q','g','h'],4,'unknown')
    assert select_examples(records,cards)[0][0]['id'].startswith('neutral-')
    dragons = rows(['q','d','e'],2,'dragons',hero='The Dragons')
    for cid in ('q','e'): cards[cid]['heroes']=['Hero8']
    assert select_examples(dragons,cards)[0][1] == 2


def test_one_item_variants_merge_but_do_not_chain_through_another_variant():
    records = rows(['q','a','b'],5,'a') + rows(['q','a','c'],4,'b') + rows(['q','d','c'],3,'c')
    selected = select_examples(records,catalog('q','a','b','c','d'))
    assert [count for _,count,_ in selected] == [9,3]
    assert selected[0][0]['id'].startswith('a-')
    assert sum(count for _,count,_ in selected) == 12
    for left,_,_ in selected:
        for right,_,_ in selected:
            if left is not right:
                assert not one_item_apart(tuple(sorted(c['cardId'] for c in left['items'])),tuple(sorted(c['cardId'] for c in right['items'])))


def test_add_delete_replace_ignore_metadata_but_preserve_duplicate_items_and_heroes():
    assert one_item_apart(('a','b'),('a','b','c'))
    assert one_item_apart(('a','b'),('a','c'))
    assert not one_item_apart(('a','a','b'),('a','b','b','c'))
    records=rows(['q','a'],3,'base')
    variant=copy.deepcopy(records[0]);variant['id']='quality';variant['items'][0].update(tier='Bronze',enchantment='Fiery',slotPosition=9)
    variant['skills']=[];records.append(variant)
    records+=rows(['q','a'],2,'other',hero='Dooley')
    result=select_examples(records,catalog('q','a'))
    assert [count for _,count,_ in result]==[4,2]


def test_only_ten_wins_and_real_most_frequent_exact_board_are_represented():
    records=rows(['q','a','b'],4,'common')+rows(['q','a','c'],1,'rare-native',date='2026-10-20T00:00:00Z')+rows(['q','d','e'],100,'losing',wins=8)
    cards=catalog('q','a','b','c','d','e');cards['b']['heroes']=['Dooley']
    original=copy.deepcopy(records)
    engine=Analysis(dict(runs=records,cards=cards,season=19,phase='19.2',captured_at='2026-10-20T00:00:00Z',source='test'))
    examples=engine.examples(engine.runs)
    assert len(examples)==1
    example=examples[0]
    assert example['id'].startswith('common-')
    assert example['similar_ten_wins']==5 and example['exact_ten_wins']==4
    source=next(row for row in records if row['id']==example['id'])
    assert example['screenshot']==source['screenshot'] and example['skills'][0]['tier']=='Silver'
    assert example['items'][0]['tier']=='Gold' and records==original
    assert select_examples(rows(['q','a'],10,wins=5),cards)==[]


def test_results_are_deterministic_when_input_order_changes():
    records=rows(['q','a','b'],3,'a')+rows(['q','a','c'],3,'b')+rows(['q','d','e'],3,'c')
    cards=catalog('q','a','b','c','d','e')
    assert select_examples(records,cards)==select_examples(list(reversed(records)),cards)
