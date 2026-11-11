"""Exact closed itemsets plus a small MDL code-table experiment, stdlib only."""
from collections import Counter
from math import log2


def vertical(transactions):
    masks={}
    for i,row in enumerate(transactions):
        for item in row:masks[item]=masks.get(item,0)|(1<<i)
    return masks


def closed_itemsets(transactions,min_support=30):
    masks=vertical(transactions)
    items=sorted((c for c,m in masks.items() if m.bit_count()>=min_support),key=lambda c:(masks[c].bit_count(),c))
    tid=[masks[c] for c in items];allbits=(1<<len(transactions))-1
    root=frozenset(j for j,m in enumerate(tid) if m==allbits)
    output={};nodes=0
    def visit(intent,extent,last):
        nonlocal nodes
        nodes+=1
        if len(intent)>=2:
            output[tuple(sorted(items[j] for j in intent))]=extent
        for i in range(last+1,len(items)):
            if i in intent:continue
            child=extent&tid[i]
            if child.bit_count()<min_support:continue
            added={j for j,m in enumerate(tid) if j not in intent and child&m==child}
            if any(j<i for j in added):continue
            visit(intent|added,child,i)
    if transactions:visit(root,allbits,-1)
    return output,masks,dict(nodes=nodes,closed=len(output),items=len(items))


def code_length(patterns,masks,singleton_lengths):
    # Bitset cover is equivalent to visiting every transaction with this fixed
    # pattern order, but requires no Python loop over individual boards.
    available=dict(masks);usage=Counter()
    ordered=sorted(patterns,key=lambda p:(-len(p),-patterns[p].bit_count(),p))
    for pattern in ordered:
        matched=available[pattern[0]]
        for item in pattern[1:]:matched &= available[item]
        if not matched:continue
        usage[pattern]=matched.bit_count()
        for item in pattern:available[item]&=~matched
    for item,bits in available.items():
        if bits:usage[(item,)]=bits.bit_count()
    total=sum(usage.values())
    if not total:return 0.0,usage
    data=total*log2(total)-sum(n*log2(n) for n in usage.values())
    model=sum(log2(total/n)+sum(singleton_lengths[c] for c in p)+log2(len(p)+1) for p,n in usage.items())
    return data+model,usage


def mdl_select(closed,transactions,masks,max_candidates=None):
    boards=Counter(frozenset(row) for row in transactions)
    compressed=list(boards.items())
    tokens=sum(len(row)*n for row,n in compressed)
    lengths={c:-log2(m.bit_count()/tokens) for c,m in masks.items()}
    # Candidate order is an upper-bound style saving estimate, not a claim of
    # exact SLIM/CHARM implementation. Each accepted entry must lower exact MDL.
    candidates=sorted(closed,key=lambda p:(-((len(p)-1)*closed[p].bit_count()-sum(lengths[c] for c in p)),p))
    if max_candidates is not None:candidates=candidates[:max_candidates]
    accepted={};current,usage=code_length(accepted,masks,lengths);initial=current;history=[]
    for pattern in candidates:
        trial=dict(accepted);trial[pattern]=closed[pattern]
        score,new_usage=code_length(trial,masks,lengths)
        if score+1e-9<current:
            history.append(dict(core=list(pattern),gain=current-score))
            accepted=trial;current=score;usage=new_usage
    # Prune patterns that became unused or redundant after later additions.
    for pattern in list(accepted):
        trial=dict(accepted);trial.pop(pattern)
        score,new_usage=code_length(trial,masks,lengths)
        if score<=current+1e-9:
            accepted=trial;current=score;usage=new_usage
    return accepted,dict(initial_bits=initial,final_bits=current,candidates_tested=len(candidates),candidate_limit=max_candidates,closed_candidates=len(closed),selected=len(accepted),history=history,usage={','.join(p):usage[p] for p in accepted})
