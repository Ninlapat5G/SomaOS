"""What the tuning numbers actually do, measured on a store you can rebuild.

Run:  python -m somaos.bench.experiments.tuning_sweep

MAX_CHILDREN decides how wide a node may get before consolidation splits
it, which is the same as deciding how deep the tree is. Wide means few
steps per recall but many comparisons at each one; deep means the reverse.
The column that settles it is comparisons, not steps: a walk that takes two
steps and compares the cue against every memory at each one has hidden the
linear scan rather than avoided it.

The flat row at the bottom of each table is the control -- no tree at all,
every memory compared once. Anything above it costing fewer comparisons at
the same hit rate is the tree earning its keep.
"""
import numpy as np

from somaos.broker.consolidation import ConsolidationMachine
from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import embed
from somaos.broker.recall import RecallMachine, Move
ROOMY = 10**8

def build(n, max_children, beam=4):
    tree = MemoryTree(beam=beam)
    root = tree.insert(make_node(region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
                                 vec=embed(("life",)), keys=("life",)))
    targets = [(tree.insert(make_node(region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
                vec=embed(("life", f"e{i}")), keys=("life", f"e{i}"), span=(i, i)),
                parent=root, tick=i), f"e{i}") for i in range(n)]
    m = ConsolidationMachine(dilution=DilutionEngine(store_budget_bytes=ROOMY), max_children=max_children)
    for _ in range(8):
        m.run(tree, tick=n + 1, window=10**6)
    return tree, targets

def probe(tree, key, target, ops=32):
    tree.reset_comparisons()
    r = RecallMachine(tree, ops_budget=ops, context_budget_tokens=10**6)
    r.begin(topics=("life",), entities=(key,), tick=10**5)
    hit = tree.alias.resolve(r.position or "") == tree.alias.resolve(target)
    while Move.DESCEND in r.offer() and not hit:
        r.step(Move.DESCEND)
        hit = tree.alias.resolve(r.position) == tree.alias.resolve(target)
    steps = r.path.ops_used
    r.finish()
    return hit, tree.comparisons, steps

def main() -> int:
    for N in (120, 600):
        print(f"=== คลัง {N} ความทรงจำ · beam=4 ===")
        print(f"{'MAX_CHILDREN':>13} {'ลูกมากสุด':>10} {'ลึกสุด':>7} {'หาเจอ':>7} "
              f"{'เทียบเวกเตอร์':>14} {'ก้าวที่เดิน':>11}")
        for mc in (4, 8, 12, 24, 48, N):
            tree, targets = build(N, mc)
            kids = max(len(tree.children_of(x)) for x in tree.region_members(Region.ARCHIVE))
            depths = [tree.depth_of(a) for a, _ in targets if a in tree]
            res = [probe(tree, k, a) for a, k in targets[:50]]
            hits = np.mean([r[0] for r in res]); comps = np.mean([r[1] for r in res])
            steps = np.mean([r[2] for r in res])
            label = f"{mc} (แบน)" if mc == N else str(mc)
            print(f"{label:>13} {kids:>10} {max(depths):>7} {100*hits:>6.0f}% "
                  f"{comps:>14.0f} {steps:>11.1f}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
