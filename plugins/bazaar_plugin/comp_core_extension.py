"""Extend learned cores using co-occurrence and two chronological samples."""

MIN_MATCHES = 30
MIN_PER_HALF = 15
MIN_BRANCH_COVERAGE = .30
MIN_PROMOTION_COVERAGE = .65
MIN_CANDIDATE_GAP = .15
MIN_COMPANION_GAP = .10
MAX_EXTRA_ITEMS = 3


def core_key(core):
    return ','.join(sorted(core))


def _stable_companion(cards, excluded, matches, misses, halves):
    populations = [(matches, misses), *((matches & half, misses & half) for half in halves)]
    counts = [(left.bit_count(), right.bit_count()) for left, right in populations]
    if min(min(pair) for pair in counts) < MIN_PER_HALF:
        return False
    for cid, bits in cards.items():
        if cid in excluded or (bits & (matches | misses)).bit_count() < MIN_PER_HALF:
            continue
        gaps = [(bits & left).bit_count() / nl - (bits & right).bit_count() / nr
                for (left, right), (nl, nr) in zip(populations, counts)]
        if min(gaps) >= MIN_COMPANION_GAP or max(gaps) <= -MIN_COMPANION_GAP:
            return True
    return False


def _choose(cards, required, base, reference, halves):
    total, background = base.bit_count(), reference.bit_count()
    if not background or total < MIN_MATCHES:
        return None
    companions = sorted(
        ((-(bits & base).bit_count(), cid) for cid, bits in cards.items() if cid not in required),
    )[:5]
    branch = None
    for negative_count, cid in companions:
        count = -negative_count
        coverage = count / total
        if count < MIN_MATCHES or coverage < MIN_BRANCH_COVERAGE:
            break
        bits = cards[cid]
        if coverage - (bits & reference).bit_count() / background < MIN_CANDIDATE_GAP:
            continue
        promote = coverage >= MIN_PROMOTION_COVERAGE
        minimum = MIN_PROMOTION_COVERAGE if promote else MIN_BRANCH_COVERAGE
        stable = True
        for half in halves:
            trial, control = base & half, reference & half
            both = (bits & trial).bit_count()
            if both < MIN_PER_HALF or control.bit_count() < MIN_PER_HALF:
                stable = False
                break
            rate = both / trial.bit_count()
            if rate < minimum or rate - (bits & control).bit_count() / control.bit_count() < MIN_CANDIDATE_GAP:
                stable = False
                break
        if not stable:
            continue
        if promote:
            return cid, True
        if branch is None:
            misses = base & ~bits
            if misses.bit_count() >= MIN_MATCHES and _stable_companion(
                cards, required | {cid}, base & bits, misses, halves,
            ):
                branch = cid, False
    return branch


def extend_cores(anchor, families, cards, dated_boards):
    """Return only changed definitions, keyed by their original core."""
    scope = cards[anchor]
    dated = [(instant, bit) for instant, bit in dated_boards if scope & bit]
    if len(dated) < 2 * MIN_PER_HALF:
        return {}
    midpoint = dated[len(dated) // 2][0]
    early = sum(bit for instant, bit in dated if instant < midpoint)
    late = sum(bit for instant, bit in dated if instant >= midpoint)
    halves = early, late

    extensions = {}
    for family in families:
        for core in family:
            if len(core) >= MAX_EXTRA_ITEMS:
                continue
            base = scope
            for cid in core:
                base &= cards[cid]
            # A mined three-card core has no unique parent. Use all its
            # one-card omissions for the comparison against the longer core.
            reference = 0
            for omitted in core:
                shorter = scope
                for cid in core:
                    if cid != omitted:
                        shorter &= cards[cid]
                reference |= shorter & ~base

            required = set(core) | {anchor}
            display = list(core)
            branches = []
            while len(required) <= MAX_EXTRA_ITEMS:
                choice = _choose(cards, required, base, reference, halves)
                if choice is None:
                    break
                cid, promote = choice
                required.add(cid)
                reference, base = base & ~cards[cid], base & cards[cid]
                expanded = sorted(required - {anchor})
                if not promote:
                    branches.append(expanded)
                elif branches:
                    branches[-1] = expanded
                else:
                    display = expanded
            if display != core or branches:
                extensions[core_key(core)] = {'core': display, 'branches': branches}
    return extensions
