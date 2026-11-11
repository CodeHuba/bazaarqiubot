"""Learn groups of related terminal builds from the current patch."""

from collections import defaultdict
from functools import reduce
from math import log2
from operator import and_
from random import Random

from .comp_patterns import closed_itemsets, mdl_select

MIN_RUNS = 30


def divergence(rates):
    left = sum(a for a, _ in rates)
    right = sum(b for _, b in rates)
    if not left or not right:
        return 1.0
    distance = 0.0
    for a, b in rates:
        p, q = a / left, b / right
        midpoint = (p + q) / 2
        if p:
            distance += .5 * p * log2(p / midpoint)
        if q:
            distance += .5 * q * log2(q / midpoint)
    return distance


class HeroPatterns:
    def __init__(self, hero, boards):
        self.hero = hero
        self.count = len(boards)
        closed, self.cards, _ = closed_itemsets(boards, MIN_RUNS)
        self.modules, _ = mdl_select(closed, boards, self.cards)
        self.card_features = [(frozenset({cid}), bits) for cid, bits in self.cards.items()
                              if bits.bit_count() >= MIN_RUNS]
        self.module_features = [(frozenset(core), bits) for core, bits in self.modules.items()]
        rng = Random('context-stability-' + hero)
        self.samples = [sum(1 << i for i in range(self.count) if rng.random() < .5)
                        for _ in range(10)]

    def roots(self, anchor):
        support = self.cards[anchor]
        anchor_count = support.bit_count()
        matches = defaultdict(set)
        for module in self.modules:
            extra = set(module) - {anchor}
            if not extra:
                continue
            background = reduce(and_, (self.cards[cid] for cid in extra))
            joint = support & background
            count = joint.bit_count()
            if count >= MIN_RUNS and count * self.count >= 1.1 * anchor_count * background.bit_count():
                matches[joint].update(extra)
        return sorted(
            [(frozenset(core), bits) for bits, core in matches.items()
             if not any(other < core for other in matches.values())],
            key=lambda root: (-root[1].bit_count(), sorted(root[0])),
        )

    def compare(self, anchor, left, right, mask, minimum):
        left_cards, left_bits = left
        right_cards, right_bits = right
        left_bits &= mask
        right_bits &= mask
        smaller = min(left_bits.bit_count(), right_bits.bit_count())
        if not smaller:
            return False, 1.0
        common = (left_bits & right_bits).bit_count()
        overlap = common / smaller
        a, b = left_bits & ~right_bits, right_bits & ~left_bits
        ln, rn = a.bit_count(), b.bit_count()
        if min(ln, rn) < minimum:
            return overlap >= .8, 0.0

        excluded = left_cards | right_cards | {anchor}

        def features(source):
            rows = []
            for cards, bits in source:
                if excluded.isdisjoint(cards):
                    p, q = (a & bits).bit_count() / ln, (b & bits).bit_count() / rn
                    if max(p, q) >= .05:
                        rows.append((cards, p, q))
            return rows

        card_rates = features(self.card_features)
        if len(card_rates) < 10:
            return False, 1.0
        differences = sorted(card_rates, key=lambda row: -abs(row[1] - row[2]))
        largest, second = [abs(p - q) for _, p, q in differences[:2]]
        card_distance = divergence([(p, q) for _, p, q in card_rates])
        module_rates = features(self.module_features)
        if (largest <= .25 and card_distance <= .10
                and (len(module_rates) < 5 or divergence([(p, q) for _, p, q in module_rates]) <= .15)):
            return True, card_distance

        eligible = (self.cards[anchor] & mask).bit_count()
        if (overlap < .40 or common * eligible < left_bits.bit_count() * right_bits.bit_count()
                or largest > .50 or second > .25):
            return False, card_distance
        outlier = differences[0][0]
        trimmed_cards = [(p, q) for cards, p, q in card_rates if cards.isdisjoint(outlier)]
        trimmed_modules = [(p, q) for cards, p, q in module_rates if cards.isdisjoint(outlier)]
        return (len(trimmed_cards) >= 10 and len(trimmed_modules) >= 5
                and divergence(trimmed_cards) <= .10 and divergence(trimmed_modules) <= .15), card_distance

    def families(self, anchor):
        roots = self.roots(anchor)
        full = self.cards[anchor]
        groups = []
        for root in roots:
            eligible = []
            for group in groups:
                scores = []
                for member in group:
                    compatible, distance = self.compare(anchor, root, member, full, MIN_RUNS)
                    if not compatible:
                        break
                    votes = 0
                    for index, sample in enumerate(self.samples):
                        votes += self.compare(anchor, root, member, sample, 15)[0]
                        if votes >= 8 or index + 1 - votes > 2:
                            break
                    if votes < 8:
                        break
                    scores.append(distance)
                if len(scores) == len(group):
                    eligible.append((sum(scores) / len(scores), len(eligible), group))
            if eligible:
                min(eligible, key=lambda choice: choice[:2])[2].append(root)
            else:
                groups.append([root])
        return sorted(sorted(sorted(core) for core, _ in group) for group in groups)
