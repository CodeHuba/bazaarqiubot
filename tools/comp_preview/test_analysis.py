import copy
import unittest
from analysis import Analysis, lower_bound


def run(i, items, wins=10, skills=('s1','s2'), hero='Vanessa', created='2026-10-18T00:00:00Z', rank='Legendary'):
    return {'id': str(i), 'hero': hero, 'created_at': created, 'wins': wins, 'rank': rank,
            'items': [{'cardId': c, 'tier': 'Gold', 'slotPosition': n} for n,c in enumerate(items)],
            'skills': [{'cardId': c, 'tier': 'Silver'} for c in skills], 'screenshot': '/'+str(i)+'.webp'}


def engine(rows):
    return Analysis({'runs': rows, 'cards': {c: {'name':c, 'internalName': c} for r in rows for c in
                    [x['cardId'] for x in r['items']+r['skills']]}, 'season':19, 'phase':'19.2',
                    'captured_at':'2026-10-19T00:00:00Z', 'source':'test'})


class AnalysisTest(unittest.TestCase):
    def test_repeated_identical_builds_count_each_run_and_two_card_group_is_visible(self):
        result = engine([run(i, ['q','a']) for i in range(100)]).query(['q'])
        self.assertEqual(result['baseline']['count'],100)
        self.assertEqual(result['groups'][0]['count'],100)
        self.assertEqual([c['cardId'] for c in result['groups'][0]['core']], ['q','a'])

    def test_ten_repetitions_are_not_lost_among_ninety_unique_builds(self):
        rows = [run(i,['q','a','b']) for i in range(10)] + [run(i+10,['q',f'x{i}',f'y{i}']) for i in range(90)]
        result = engine(rows).query(['q'])
        self.assertTrue(any(g['count']==10 and {c['cardId'] for c in g['core']}=={'q','a','b'} for g in result['groups']))
        self.assertEqual(result['groups'][0]['sample_level'],'explore')

    def test_multiple_items_are_anded_and_skills_do_not_match_input(self):
        e = engine([run(i,['q','a']) for i in range(40)] + [run(i+100,['q','b']) for i in range(40)])
        self.assertEqual(e.query(['q','a'])['baseline']['count'],40)
        self.assertEqual(e.query(['a','b'])['baseline']['count'],0)
        self.assertEqual(e.search('s1'),[])
        with self.assertRaises(ValueError): e.query(['s1'])

    def test_all_filters_and_days_use_snapshot_time(self):
        rows = [run(1,['q','a']),run(2,['q','a'],hero='Dooley'),run(3,['q','a'],rank='Gold'),
                run(4,['q','a'],created='2026-10-01T00:00:00Z'),run(5,['q','a'],created='bad')]
        self.assertEqual(engine(rows).query(['q'],'Vanessa','legendary',7)['baseline']['count'],1)

    def test_one_run_and_one_skill_presence_are_counted_once(self):
        row = run(1,['q','a','a'],skills=['s1','s1','s2'])
        result = engine([row,copy.deepcopy(row)]).query(['q'])
        self.assertEqual(result['baseline']['count'],1)
        self.assertEqual(len(result['examples']),1)
        self.assertEqual(len(result['examples'][0]['skills']),3)

    def test_metrics_match_raw_records_and_examples_are_same_run(self):
        rows = [run(i,['q','a'],wins=10 if i<25 else 5) for i in range(40)]
        e=engine(rows)
        result=e.query(['q'])
        g=result['groups'][0]
        self.assertEqual((g['count'],g['ten_wins'],g['rate'],g['average_wins']),(40,25,25/40,325/40))
        for example in g['examples']:
            source=next(r for r in rows if r['id']==example['id'])
            self.assertEqual(example['screenshot'],source['screenshot'])
            self.assertEqual(example['items'][0]['tier'],'Gold')
            self.assertEqual(example['skills'][0]['tier'],'Silver')

    def test_removed_skill_pair_and_all_sample_blocks_are_not_computed(self):
        rows=[run(i,['q','a'],skills=['s1']) for i in range(30)]+[run(i+100,['q','a'],skills=['s2']) for i in range(30)]
        e=engine(rows)
        e.skill_pairs=lambda *args: self.fail('Removed blocks must not be computed')
        result=e.query(['q'])
        self.assertFalse({'items','skills','skill_pairs'} & result.keys())
        self.assertFalse({'items','skills','skill_pairs','winning_skill_pairs'} & result['groups'][0].keys())

    def test_small_sample_is_separate_and_wilson_is_conservative(self):
        self.assertLess(lower_bound(3,3),lower_bound(80,100))
        result=engine([run(i,['q','a']) for i in range(9)]).query(['q'])
        self.assertEqual(result['groups'],[])
        self.assertTrue(result['examples'])
        self.assertGreater(lower_bound(100,100),.96)

    def test_winning_companions_exclude_non_winning_runs(self):
        rows = [run(i, ['q','a',f'x{i}'], skills=['w1','w2'] if i < 10 else ['w1']) for i in range(20)]
        rows += [run(i, ['q','a',f'x{i}'], wins=3, skills=['loss1','loss2']) for i in range(20,40)]
        group = engine(rows).query(['q'])['groups'][0]
        self.assertEqual((group['count'],group['ten_wins'],group['rate'],group['average_wins']), (40,20,.5,6.5))
        self.assertEqual({c['cardId'] for c in group['winning_skills']}, {'w1','w2'})
        self.assertTrue(all(c['cardId'] not in {f'x{i}' for i in range(20,40)} for c in group['winning_items']))
        self.assertEqual(next(c for c in group['winning_skills'] if c['cardId']=='w2')['ten_win_frequency'],.5)

    def test_no_ten_win_samples_do_not_fall_back_to_other_runs(self):
        group = engine([run(i,['q','a'],wins=5) for i in range(30)]).query(['q'])['groups'][0]
        self.assertEqual(group['count'],30)
        self.assertEqual(group['ten_wins'],0)
        for key in ('winning_items','winning_skills'):
            self.assertEqual(group[key],[])

    def test_sorting_is_before_display_limit_and_deterministic(self):
        rows=[]
        for k in range(24):
            rows.extend(run(f'{k}-{i}',['q',f'a{k}'],10 if i<k+8 else 3) for i in range(40))
        e=engine(rows)
        result=e.query(['q'],sort='rate')
        self.assertEqual(result['groups'][0]['extra_ids'],['a23'])
        self.assertEqual(result,e.query(['q'],sort='rate'))

    def test_invalid_conditions_fail_explicitly(self):
        e=engine([run(1,['q','a'])])
        for kwargs in ({'days':0},{'days':1.5},{'rank':'bad'},{'hero':'bad'},{'sort':'bad'}):
            with self.assertRaises(ValueError): e.query(['q'],**kwargs)


if __name__ == '__main__':
    unittest.main()
