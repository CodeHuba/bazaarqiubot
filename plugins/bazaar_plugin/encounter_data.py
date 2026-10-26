"""Official-language merchant and non-combat encounter documents."""
from contextlib import closing
from copy import deepcopy
import difflib
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from . import gamedata_client as game
from .card_preview import CardPreviewRepository, QUALITY_LABELS, _plain, _fingerprint

SOURCES = Path(__file__).with_name('encounter_sources.json')
TYPES = ('EventEncounter', 'PedestalEncounter')
SECTIONS = {'SelfHandAndStash':'场上及储藏区', 'AbsolutePlayerHandAndStash':'场上及储藏区',
            'SelfHand':'场上', 'AbsolutePlayerHand':'场上', 'AbsolutePlayerStash':'储藏区',
            'SelfStash':'储藏区', 'AbsolutePlayerSkills':'已拥有的技能', 'SelectionSet':'当前选项'}
COMPARE = {'Equal':'等于','NotEqual':'不等于','GreaterThan':'大于','GreaterThanOrEqual':'至少',
           'LessThan':'小于','LessThanOrEqual':'至多'}

def hidden(raw):
    return any(s in raw.get('InternalName','').casefold() for s in ('tutorial','[debug]','start run','level up'))

def fixed(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):return value
    if isinstance(value,dict) and value.get('$type')=='TFixedValue':return value.get('Value')
    return None

def flatten_actions(action):
    if not isinstance(action,dict):return
    if action.get('$type')=='TActionAnd':
        for child in action.get('Actions') or []:yield from flatten_actions(child)
    else:yield action

def classify(raw, cards):
    if 'Merchant' in (raw.get('Tags') or []):return 'merchant'
    context=raw.get('SelectionContext') or {}
    # A paid item/skill shop can be tagged as EventEncounter without Merchant.
    if (context.get('Rules') or {}).get('CanSelectMultiple') and (context.get('Rules') or {}).get('SelectionIsFree') is False:
        ids=[i for g in (context.get('SpawnContext') or {}).get('Groups',[]) for f in g.get('Filters',[]) for i in f.get('Ids',[])]
        if ids and all((cards.get(i) or {}).get('Type') in ('Item','Skill') for i in ids):return 'merchant'
        def contains_stock(value):
            if isinstance(value,list):return any(contains_stock(v) for v in value)
            if not isinstance(value,dict):return False
            if value.get('$type')=='ConstraintCardType' and not value.get('IsNot') and set(value.get('Types') or [])<=set(('Item','Skill')):return True
            return any(contains_stock(v) for v in value.values())
        if contains_stock(context.get('SpawnContext')):return 'merchant'
    return 'event'

class EncounterRepository:
    def __init__(self,database,translations=None,images=None,sources=SOURCES):
        self.database=Path(database)
        self.preview=CardPreviewRepository(database,translations,images)
        self.sources=Path(sources)
        self._lock=threading.RLock()
        self._stamp=''
        self._cards={}
        self._entries=[]
        self._art={}
        self._days={}

    def _refresh(self):
        self.preview._refresh()
        stamp=self.preview._stamp+'|'+_fingerprint(self.sources)
        if stamp==self._stamp:return
        with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            self._cards={d['Id']:d for (s,) in db.execute('SELECT Data FROM cards') for d in [json.loads(s)]}
        source=json.loads(self.sources.read_text(encoding='utf-8')) if self.sources.is_file() else {}
        self._art=source.get('entries',{})
        self._days=source.get('appearance',{})
        entries=[]
        for raw in self._cards.values():
            if raw.get('Type') not in TYPES or hidden(raw):continue
            name=self.name(raw)
            if not name:continue
            title=((raw.get('Localization') or {}).get('Title') or {}).get('Text','')
            entries.append({'id':raw['Id'],'name':name,'name_en':title,'kind':classify(raw,self._cards),
                            'aliases':list({game.normalize_card_name(v) for v in [name,title,raw.get('InternalName','')] if v}),
                            'heroes':self.heroes(raw),'tier':raw.get('StartingTier'),'raw':raw})
        self._entries=entries
        self._stamp=stamp

    def translate(self,content):
        return _plain(self.preview._translate(content or {},'zh-CN'))

    def name(self,raw):
        return self.translate((raw.get('Localization') or {}).get('Title')) or raw.get('InternalName','')

    def word(self,value):
        if value in QUALITY_LABELS:return QUALITY_LABELS[value]
        return {'Small':'小型','Medium':'中型','Large':'大型','Item':'物品','Skill':'技能','Common':'通用',
                'Gold':'黄金','HealthMax':'最大生命值','Health':'生命值','Experience':'经验','Income':'收入',
                'BuyPrice':'购买价格','SellPrice':'价值','CooldownMax':'冷却','Multicast':'多重施法',
                'ShieldApplyAmount':'护盾','HealAmount':'治疗','RegenApplyAmount':'回复'}.get(value) or self.translate({'Text':str(value)})

    def heroes(self,raw):
        return '全部英雄' if 'Common' in (raw.get('Heroes') or []) else '、'.join(self.word(v) for v in raw.get('Heroes') or []) or '资料暂缺'

    def search(self,name,exact=True,kind=''):
        with self._lock:
            self._refresh();q=game.normalize_card_name(name)
            if not q:return []
            found=[]
            for entry in self._entries:
                if kind and entry['kind']!=kind:continue
                score=max((difflib.SequenceMatcher(None,q,a).ratio()+(.4 if q in a else 0) for a in entry['aliases']),default=0)
                if (exact and q in entry['aliases']) or (not exact and (any(q in a for a in entry['aliases']) or score>=.65)):
                    found.append((score,entry))
            found.sort(key=lambda pair:(-pair[0],pair[1]['name'],pair[1]['id']))
            return [deepcopy(e) for _,e in found]

    def candidate(self,entry):
        return {'Id':entry['id'],'Type':entry['kind'],'InternalName':entry['name_en'],
                'Localization':{'Title':{'Text':entry['name']}}}

    def value(self,value):
        number=fixed(value)
        if number is not None:return game.fmt_val(number)
        if not isinstance(value,dict):return '动态数值'
        if value.get('$type')=='TRangeValue':return f"{game.fmt_val(value.get('MinValue'))}–{game.fmt_val(value.get('MaxValue'))}（随机）"
        if value.get('AttributeType'):return '当前'+self.word(value['AttributeType'])+'相关数值'
        return '动态数值'

    def condition(self,data):
        if not data:return ''
        if isinstance(data,list):return '；'.join(filter(None,(self.condition(d) for d in data)))
        if not isinstance(data,dict):return str(data)
        typ=data.get('$type','')
        children=data.get('Conditions') or data.get('Constraints')
        if children:
            if isinstance(children,dict):return self.condition(children)
            join=' 或 ' if typ.endswith('Or') else ' 且 '
            return join.join(filter(None,(self.condition(d) for d in children)))
        text=''
        for key,label in [('Sizes','尺寸'),('Tiers','品质'),('Heroes','英雄'),('Types','类型'),('Tags','效果/类别')]:
            if data.get(key):
                join=' 或 ' if data.get('Operator')!='All' else ' 且 '
                text=label+'为'+join.join(self.word(v) for v in data[key]);break
        if data.get('Id'):
            text='拥有'+self.name(self._cards.get(data['Id'],{'InternalName':'指定物品'}))
        if data.get('Attribute'):
            text=self.word(data['Attribute'])+COMPARE.get(data.get('ComparisonOperator'),'满足')+self.value(data.get('ComparisonValue'))
        if 'CurrentDay' in data:
            text='天数'+COMPARE.get(data.get('ComparisonOperator'),'满足')+' Day '+str(data['CurrentDay'])
        if data.get('Enchantment'):
            enchant=self.word(data['Enchantment'])
            text='可附加'+enchant+'附魔' if 'Eligible' in typ else '已有'+enchant+'附魔'
        if typ=='TPrerequisiteCardCount':
            text=self.target(data.get('Subject'))+'数量'+COMPARE.get(data.get('Comparison'),'满足')+str(data.get('Amount','动态数值'))
        if typ=='TPrerequisitePlayerAttribute':
            text=self.word(data.get('AttributeType','属性'))+COMPARE.get(data.get('Comparison'),'满足')+self.value(data.get('Value'))
        if not text:text='附加条件（游戏内判定）'
        return ('不满足：' if data.get('IsNot') else '')+text

    def target(self,data):
        if not data:return ''
        section=SECTIONS.get(data.get('TargetSection'),'物品')
        mode={'LeftMostCard':'最左侧','RightMostCard':'最右侧','HighestValue':'最高价值','LowestValue':'最低价值'}.get(data.get('TargetMode'),'')
        typ=data.get('$type','')
        if 'Random' in typ:mode='随机'
        if typ=='TTargetCardSelf':section='此物品'
        if typ.startswith('TTargetPlayer'):section='玩家'
        condition=self.condition(data.get('Conditions'))
        return section+('的'+mode if mode else '')+('（'+condition+'）' if condition else '')

    def spawn_summary(self,context):
        if not context:return '没有额外商品范围限制'
        if context.get('$type')=='TSpawnContextMirror':return '沿用当前遭遇的选项'
        lines=[]
        for group in context.get('Groups') or []:
            parts=[]
            for filt in group.get('Filters') or []:
                if filt.get('Constraints'):parts.append(self.condition(filt['Constraints']))
                if filt.get('Ids'):
                    types={self._cards.get(i,{}).get('Type') for i in filt['Ids']}
                    parts.append('指定'+('物品' if types=={'Item'} else '技能' if types=={'Skill'} else '选项'))
            for behavior in group.get('Behaviors') or []:
                if behavior.get('Tiers'):parts.append('品质：'+'、'.join(self.word(t) for t in behavior['Tiers']))
            prerequisites=self.condition(group.get('Prerequisites'))
            if prerequisites:parts.append('条件：'+prerequisites)
            line='；'.join(dict.fromkeys(parts))
            if line and line not in lines:lines.append(line)
        for behavior in context.get('Behaviors') or []:
            if behavior.get('Tiers'):lines.append('商品品质：'+'、'.join(self.word(t) for t in behavior['Tiers']))
            if behavior.get('IgnoreHero'):lines.append('商品可以来自任意英雄')
        return '\n'.join(lines) or '随机选项，范围随当前状态确定'

    def art(self,raw):
        info=self._art.get(raw.get('Id')) or {}
        if info.get('art_key')!=raw.get('ArtKey'):info={}
        if not info and raw.get('Type') in ('Item','Skill'):info=self.preview._images.get(raw['Id']) or {}
        return info.get('artLarge') or info.get('art') or '',info.get('art_fallback','')

    def effects(self,raw,quality=''):
        loc=raw.get('Localization') or {};attrs=raw.get('Attributes') or {}
        texts=[]
        sources=[loc.get('Description')]+[(t.get('Content') or {}) for t in loc.get('Tooltips') or []]
        for source in sources:
            if not source:continue
            content=self.translate(source)
            if content:
                rendered=game.render_tooltip(content,raw.get('Abilities') or {},raw.get('Auras') or {},attrs)
                rendered=re.sub(r'\{[^{}]+\}','动态数值',rendered)
                rendered=rendered.replace('黄金枚金币','枚金币').replace('黄金金币','金币')
                if rendered not in texts:texts.append(rendered)
        if raw.get('Type') in ('Item','Skill'):
            try:
                preview=self.preview.get(raw['Id'],quality or raw.get('StartingTier') or '')
                texts=[(('条件：'+e['condition']+'；') if e.get('condition') else '')+e['text'] for e in preview['effects']]
            except (LookupError,ValueError):pass
        return texts

    def _option(self,raw,rules,context=None,tier=''):
        quality=tier or raw.get('StartingTier') or 'Bronze'
        attrs=game.build_tier_attrs(raw,quality) if raw.get('Tiers') else raw.get('Attributes') or {}
        price=attrs.get('BuyPrice')
        cost='免费' if rules.get('SelectionIsFree') else str(price)+' 金币' if price is not None else '费用未标注'
        url,fallback=self.art(raw)
        lines=self.effects(raw,quality)
        if raw.get('Type')=='Skill':lines.insert(0,'适用英雄：'+self.heroes(raw))
        if raw.get('Type') in TYPES:
            lines.append('后续详情：#bz db '+('商店 ' if classify(raw,self._cards)=='merchant' else '事件 ')+self.name(raw))
        requirements=self.condition((context or {}).get('Prerequisites'))
        if requirements:lines.append('出现条件：'+requirements)
        actions=[]
        abilities=list((raw.get('Abilities') or {}).values())
        many_rules=len(abilities)>8
        for ability in abilities:
            actions.extend(flatten_actions(ability.get('Action')))
            prereq=self.condition(ability.get('Prerequisites'))
            if prereq and (not many_rules or all(a.get('Prerequisites')==ability.get('Prerequisites') for a in abilities)) and '触发条件：'+prereq not in lines:
                lines.append('触发条件：'+prereq)
        for action in actions:
            if raw.get('Type') not in ('Item','Skill') and action.get('Target'):
                target={**action['Target'],'Conditions':None} if many_rules else action['Target']
                scope='作用范围：'+self.target(target)
                if scope not in lines:lines.append(scope)
            if action.get('Cost'):lines.append('额外消耗：'+self.condition(action['Cost']))
        if not lines:lines=['选择后进入后续选项。' if actions else '暂无效果说明']
        return {'id':raw['Id'],'name':self.name(raw),'type':raw.get('Type'),'quality':quality,'size':raw.get('Size','Medium'),
                'art':url,'art_fallback':fallback,'cost':cost,'lines':list(dict.fromkeys(lines)),'actions':actions}

    def _choices(self,context):
        result=[]
        for group in (context or {}).get('Groups') or []:
            for filt in group.get('Filters') or []:
                for entity_id in filt.get('Ids') or []:
                    if entity_id in self._cards:result.append((self._cards[entity_id],group))
        return result

    def _upgrade_table(self,options):
        stages=[];seen=set();cost=0;current=[self._cards[o['id']] for o in options]
        for _ in range(12):
            rewards=[r for r in current if all(a.get('Action',{}).get('$type')=='TActionCardModifyAttribute' for a in (r.get('Abilities') or {}).values()) and r.get('Attributes')]
            branch=next((r for r in current if any(a.get('Action',{}).get('$type')=='TActionGameDealCards' for a in (r.get('Abilities') or {}).values())),None)
            if not rewards or any(len(r.get('Attributes') or {})!=1 for r in rewards):return None
            signature=[(self.name(r),next(iter(r['Attributes']))) for r in rewards]
            if stages and signature!=stages[0]['signature']:return None
            stages.append({'cost':cost,'signature':signature,'values':[next(iter(r['Attributes'].values())) for r in rewards]})
            if branch is None:break
            if branch['Id'] in seen:return None
            seen.add(branch['Id'])
            price=(branch.get('Attributes') or {}).get('BuyPrice')
            if price is None:return None
            cost+=price
            action=next(a['Action'] for a in branch['Abilities'].values() if a.get('Action',{}).get('$type')=='TActionGameDealCards')
            if action.get('SpawnMode')!='Replace':return None
            current=[r for r,g in self._choices(action.get('SpawnContext'))]
        if len(stages)<2 or len(stages)>5:return None
        return {'kind':'table','title':'付费强化后的选项效果','columns':['初始']+['强化 '+str(i)+' 次' for i in range(1,len(stages))],
                'rows':[{'label':'累计强化花费','values':[str(s['cost'])+' 金币' for s in stages]}]+
                       [{'label':self.word(attribute),'values':['+'+game.fmt_val(s['values'][i]) for s in stages]} for i,(name,attribute) in enumerate(stages[0]['signature'])],
                'note':'各列为不同阶段，增益需选择后获得；强化花费与选项自身费用分别计算。'}

    def detail(self,entry):
        with self._lock:
            self._refresh();raw=self._cards[entry['id']];context=raw.get('SelectionContext') or {};rules=context.get('Rules') or {};spawn=context.get('SpawnContext') or {}
            appearance=self._days.get(raw['Id']) or {};url,fallback=self.art(raw)
            limit=fixed(spawn.get('Limit'));reroll=rules.get('RerollRules')
            rerolls=(reroll or {}).get('TotalAllowedRerolls')
            if not reroll or rerolls==0:refresh='不可刷新'
            else:
                refresh=('次数未限制' if rerolls is None else '可刷新 '+str(rerolls)+' 次')+'；起始 '+str(reroll.get('StartingCost',0))+' 金币'
                if reroll.get('CostIncrease'):refresh+='；每次增加 '+str(reroll['CostIncrease'])+' 金币'
                if reroll.get('CostMax') is not None:refresh+='；费用上限 '+str(reroll['CostMax'])+' 金币'
            blocks=[]
            facts=[('出现日期',appearance.get('day_label') or '暂无可靠日期资料'),('适用英雄',self.heroes(raw))]
            if raw['Type']=='PedestalEncounter':
                behavior=raw.get('Behavior') or {}
                facts += [('事件类型','附魔 / 升级'),('时间推进','推进一小时' if raw.get('ShouldProgressHour') else '不推进小时')]
                lines=self.effects(raw)+['目标条件：'+self.condition(raw.get('SelectionCriteria'))]
                if behavior.get('Enchantment'):lines.append('结果：附加'+self.word(behavior['Enchantment'])+'附魔')
                blocks.append({'kind':'text','title':'事件效果与条件','lines':lines})
            else:
                facts += [('选择规则',('可购买多件' if rules.get('CanSelectMultiple') else '每轮单选')+'；'+('可直接离开' if rules.get('CanExit') else '必须选择')),
                          ('刷新规则',refresh)]
                choices=self._choices(spawn)
                if entry['kind']=='merchant':
                    lines=[self.spawn_summary(spawn)]
                    if limit is not None:lines.insert(0,'每次展示 '+game.fmt_val(limit)+' 项商品')
                    lines.append('购买方式：'+('免费选择' if rules.get('SelectionIsFree') else '按商品标价购买'))
                    blocks.append({'kind':'text','title':'出售范围与购买规则','lines':lines})
                    # Fixed guaranteed offers are real merchandise, never pool examples.
                    guaranteed=bool(choices) and all(r.get('Type') in ('Item','Skill') and g.get('SelectionMethod')=='Sequential' and not g.get('Prerequisites') for r,g in choices) and limit is not None and len(choices)<=limit
                    if guaranteed:
                        tiers=[b['Tiers'][0] for b in spawn.get('Behaviors') or [] if b.get('$type')=='TSpawnBehaviorTier' and len(b.get('Tiers') or [])==1]
                        for item,group in choices:blocks.append({'kind':'option','title':'固定出售','option':self._option(item,rules,group,tiers[0] if tiers else '')})
                    tooltips=self.effects(raw)
                    desc=self.translate((raw.get('Localization') or {}).get('Description'))
                    extra=[t for t in tooltips if t!=desc]
                    if extra:blocks.append({'kind':'text','title':'商店效果','lines':extra})
                else:
                    if limit is not None:blocks.append({'kind':'text','title':'选择说明','lines':['当前选项数量：'+game.fmt_val(limit)+'；下方包含条件分支，实际出现取决于条件。']})
                    reward_choices=[(r,g) for r,g in choices if r.get('Type') in ('Item','Skill')]
                    many_rewards=len(reward_choices)>8
                    many_outcomes=not reward_choices and len(choices)>12 and limit is not None and limit<len(choices)
                    visible=[] if many_outcomes else [(r,g) for r,g in choices if not many_rewards or r.get('Type') not in ('Item','Skill')]
                    options=[self._option(r,rules,g) for r,g in visible]
                    if many_rewards:blocks.append({'kind':'text','title':'物品与技能选项','lines':['按当前条件生成物品或技能选项，实际提供数量见选择说明。']})
                    if many_outcomes:
                        labels=list(dict.fromkeys('野怪战斗' if r.get('Type')=='CombatEncounter' else '其他遭遇' if r.get('Type') in TYPES else '事件选项' for r,g in choices))
                        blocks.append({'kind':'text','title':'后续结果范围','lines':['根据当前条件与生成规则提供后续选项。','可能包含：'+'、'.join(labels)+'。']})
                    for option in options:blocks.append({'kind':'option','title':'事件选项','option':option})
                    table=self._upgrade_table(options) if options else None
                    if table:blocks.append(table)
                    else:
                        visited={raw['Id']}
                        def branches(option,depth=0):
                            if depth>=6:return
                            deals=[a for a in option['actions'] if a.get('$type')=='TActionGameDealCards']
                            possible=[(r,g) for a in deals for r,g in self._choices(a.get('SpawnContext'))]
                            if len(possible)>8:
                                names=[]
                                for child,group in possible:
                                    label={'Item':'物品','Skill':'技能','CombatEncounter':'野怪','EncounterStep':'事件选项'}.get(child.get('Type'))
                                    if not label:label='商店' if classify(child,self._cards)=='merchant' else '遭遇事件'
                                    if label not in names:names.append(label)
                                count=fixed(deals[0].get('SpawnContext',{}).get('Limit')) if len(deals)==1 else None
                                lines=[('生成 '+game.fmt_val(count)+' 项后续选项。') if count is not None else '按事件条件或随机结果生成后续选项。',
                                       '可能包含：'+'、'.join(names)+'；具体内容按当前状态与生成规则确定。']
                                blocks.append({'kind':'text','title':'后续选项：'+option['name'],'lines':lines})
                                return
                            for action in deals:
                                next_context=action.get('SpawnContext') or {}
                                current=self._choices(next_context);next_rules=action.get('SelectionContextRules') or rules
                                for child,group in current:
                                    if child['Id'] in visited:continue
                                    visited.add(child['Id'])
                                    selected=self._option(child,next_rules,group)
                                    blocks.append({'kind':'option','title':'后续分支：'+option['name'],'option':selected})
                                    branches(selected,depth+1)
                        for option in options:branches(option)
                    queries=[self.spawn_summary(spawn)] if not choices else []
                    if queries:blocks.append({'kind':'text','title':'选项范围','lines':queries})
            if not blocks:blocks=[{'kind':'text','title':'事件说明','lines':self.effects(raw) or ['暂无更多说明']}]
            return {'id':raw['Id'],'name':entry['name'],'kind':entry['kind'],'art':url,'art_fallback':fallback,
                    'description':self.translate((raw.get('Localization') or {}).get('Description')),
                    'facts':facts,'blocks':blocks,'appearance_source':appearance.get('source_title',''),
                    'stamp':hashlib.sha256((self._stamp+raw['Id']).encode()).hexdigest()[:24]}

def format_document_text(document):
    lines=[document['name']+'（'+('商店' if document['kind']=='merchant' else '遭遇事件')+'）',document['description']]
    lines += [key+'：'+value for key,value in document['facts']]
    for block in document['blocks']:
        lines.append('\n'+block['title'])
        if block['kind']=='option':
            option=block['option'];lines.append(option['name']+'｜'+option['cost']);lines.extend(option['lines'])
        elif block['kind']=='table':
            lines.append(' / '.join(block['columns']))
            lines.extend(r['label']+'：'+' / '.join(r['values']) for r in block['rows'])
            lines.append(block['note'])
        else:lines.extend(block['lines'])
    return '\n'.join(filter(None,lines))
