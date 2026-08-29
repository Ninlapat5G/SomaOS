"""The five policies, under one contract and one set of budgets (N-05, N-14).

The previous round's comparison was never fair. The tree was capped by a
surprise gate while the flat RAG baseline kept everything it ever saw and
scanned all of it for free, so it could not lose. Here every policy lives
under the same three budgets -- context tokens, store bytes, recall ops --
and every policy is charged for the vector comparisons it makes.

    B0   everything in context. The quality ceiling and the cost ceiling.
    B1   the last K tokens. The cheapest thing that works at all.
    B2   flat vector search, and when the store fills, the oldest go.
         What almost everyone actually ships.
    B2c  flat vector search, and when the store fills, random memories are
         degraded instead of dropped. The control that matters: it isolates
         "knows how to compress" from "has a structure", so a win by S has
         to come from the structure.
    S    the tree.

B2 is the only policy here that loses memories outright, which is a
deliberate departure from N-01: it is a model of the thing the project
argues against, not a citizen of the design.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from somaos.bench.lifeworld import Episode, Intention, Question
from somaos.broker.consolidation import ConsolidationMachine
from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, MemoryNode, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import (
    DEFAULT_DIM,
    Grade,
    cue_vector,
    embed,
    encode,
    nbytes,
    similarity,
)
from somaos.broker.recall import Move, RecallMachine
from somaos.broker.regions import CoreSet, Trigger, TriggerKind, TriggerRegistry

D0_BYTES = DEFAULT_DIM * 4
DEFAULT_NODE_TOKENS = 16


@dataclass
class Budgets:
    store_bytes: int
    context_tokens: int
    recall_ops: int


@dataclass
class Outcome:
    """What one recall produced, and what it cost."""

    nodes: tuple[MemoryNode, ...]
    tokens: int
    comparisons: int
    ops: int


def _episode_node(episode: Episode, level=ArchiveLevel.SPECIFIC_EVENT) -> MemoryNode:
    return make_node(
        region=Region.ARCHIVE, level=int(level), vec=embed(episode.keys),
        keys=episode.keys, span=(episode.tick, episode.tick),
        text_ref=f"t{episode.tick}: {' '.join(episode.keys)}",
    )


class _Base:
    """Shared plumbing: budgets, token pricing, trigger handling.

    Triggers are handled identically by every policy, because prospective
    memory is not what is under comparison here -- the tree is. Giving S a
    private advantage on intentions would muddy the one question the run is
    meant to answer.
    """

    name = "base"

    def __init__(self) -> None:
        self.budgets = Budgets(0, 0, 0)
        self.tokens_of: Callable[[MemoryNode], int] = lambda n: DEFAULT_NODE_TOKENS
        self.triggers = TriggerRegistry()
        self.comparisons = 0

    def reset(self, *, budgets: Budgets, tokens_of, seed_root: str) -> None:
        self.budgets = budgets
        self.tokens_of = tokens_of
        self.triggers = TriggerRegistry()
        self.comparisons = 0
        self._reset()

    def _reset(self) -> None: ...

    def intend(self, intention: Intention) -> None:
        if intention.kind == "time":
            self.triggers.arm(Trigger(
                id=intention.id, kind=TriggerKind.TIME,
                due_tick=intention.due_tick, action=intention.action,
            ))
        else:
            self.triggers.arm(Trigger(
                id=intention.id, kind=TriggerKind.EVENT,
                cue=intention.cue, action=intention.action,
            ))

    def fire(self, tick: int, cues: tuple[str, ...]) -> list[str]:
        fired = [t.id for t in self.triggers.on_tick(tick)]
        for cue in cues:
            fired.extend(t.id for t in self.triggers.on_event(cue, tick=tick))
        for tid in fired:
            self.triggers.complete(tid, tick=tick)
        return fired

    def on_tick(self, tick: int) -> None: ...

    def _fit(self, ranked: list[MemoryNode]) -> tuple[tuple[MemoryNode, ...], int]:
        """Take from the top until the context budget is spent."""
        chosen, spent = [], 0
        for node in ranked:
            cost = self.tokens_of(node)
            if spent + cost > self.budgets.context_tokens:
                continue
            chosen.append(node)
            spent += cost
        return tuple(chosen), spent

    def stats(self) -> dict:
        return {}


class _Flat(_Base):
    """Everything in one list. B0, B1, B2 and B2c differ only in how they
    handle the store filling up and how they choose what to return."""

    def _reset(self) -> None:
        self.nodes: list[MemoryNode] = []
        self.dropped = 0

    def perceive(self, episode: Episode) -> None:
        self.nodes.append(_episode_node(episode))
        self._enforce()

    def _enforce(self) -> None: ...

    def store_bytes(self) -> int:
        return sum(n.nbytes for n in self.nodes)

    def _rank(self, question: Question) -> list[MemoryNode]:
        cue = cue_vector(question.cue_topics, question.cue_entities)
        # A flat store has to look at everything to rank it. That is the
        # cost the tree claims to avoid, so it has to be counted here.
        self.comparisons += len(self.nodes)
        scored = sorted(
            ((n, similarity(cue, n.vec)) for n in self.nodes),
            key=lambda pair: (-pair[1], pair[0].addr),
        )
        return [n for n, _ in scored]

    def stats(self) -> dict:
        return {"nodes": len(self.nodes), "dropped": self.dropped}


class B0Full(_Flat):
    """Everything, most recent first. Ignores the store budget on purpose:
    it is the ceiling both baselines and S are measured against."""

    name = "B0"

    def recall(self, question: Question) -> Outcome:
        before = self.comparisons
        ranked = sorted(self.nodes, key=lambda n: (-n.span[1], n.addr))
        self.comparisons += len(self.nodes)
        nodes, tokens = self._fit(ranked)
        return Outcome(nodes, tokens, self.comparisons - before, 1)


class B1Window(_Flat):
    """The last K tokens. No search at all, so no comparisons."""

    name = "B1"

    def _enforce(self) -> None:
        while self.store_bytes() > self.budgets.store_bytes and self.nodes:
            self.nodes.pop(0)
            self.dropped += 1

    def recall(self, question: Question) -> Outcome:
        ranked = sorted(self.nodes, key=lambda n: (-n.span[1], n.addr))
        nodes, tokens = self._fit(ranked)
        return Outcome(nodes, tokens, 0, 1)


class B2Rag(_Flat):
    """Flat vector search; the oldest are dropped when the store fills.

    The shape almost everyone ships, and the one this project argues
    against: what falls off the end is gone, not faded.
    """

    name = "B2"

    def _enforce(self) -> None:
        while self.store_bytes() > self.budgets.store_bytes and self.nodes:
            self.nodes.pop(0)
            self.dropped += 1

    def recall(self, question: Question) -> Outcome:
        before = self.comparisons
        nodes, tokens = self._fit(self._rank(question))
        return Outcome(nodes, tokens, self.comparisons - before, 1)


class B2cCompressed(_Flat):
    """Flat vector search; random memories are degraded when the store fills.

    The control N-14 asks for. It compresses as well as S does and has no
    structure at all, so whatever S wins over this is what the tree is
    worth -- and whatever it does not is what compression alone was worth.
    """

    name = "B2c"

    def _reset(self) -> None:
        super()._reset()
        self._rng = np.random.default_rng(0)

    def reset(self, *, budgets, tokens_of, seed_root: str) -> None:
        super().reset(budgets=budgets, tokens_of=tokens_of, seed_root=seed_root)
        # Seeded from the run's seed so "random" is reproducible (N-15).
        self._rng = np.random.default_rng(abs(hash(seed_root)) % (2**32))

    def _enforce(self) -> None:
        guard = 0
        while self.store_bytes() > self.budgets.store_bytes and guard < 10_000:
            guard += 1
            candidates = [i for i, n in enumerate(self.nodes) if n.grade < Grade.D2_BINARY]
            if not candidates:
                self.nodes.pop(0)
                self.dropped += 1
                continue
            i = int(self._rng.choice(candidates))
            node = self.nodes[i]
            nxt = Grade(node.grade + 1)
            self.nodes[i] = make_node(
                region=node.region, level=node.level,
                vec=encode(node.vec, nxt), grade=nxt, original=node.vec,
                keys=node.keys, span=node.span, text_ref=node.text_ref,
            )

    def recall(self, question: Question) -> Outcome:
        before = self.comparisons
        nodes, tokens = self._fit(self._rank(question))
        return Outcome(nodes, tokens, self.comparisons - before, 1)


class STree(_Base):
    """The tree: structure, graded fading, a bounded walk, consolidation."""

    name = "S"

    def __init__(self, *, consolidate_every: int = 25, beam: int = 4, **kwargs) -> None:
        super().__init__()
        self.consolidate_every = consolidate_every
        self.beam = beam
        self.kwargs = kwargs

    def _reset(self) -> None:
        self.tree = MemoryTree(beam=self.beam)
        self.dilution = DilutionEngine(store_budget_bytes=self.budgets.store_bytes)
        self.core = CoreSet(quota_bytes=max(D0_BYTES, self.budgets.store_bytes // 10))
        self.consolidation = ConsolidationMachine(
            dilution=self.dilution, core=self.core, **self.kwargs
        )
        self._periods: dict[str, str] = {}

    def _period_node(self, episode: Episode) -> str:
        """One general-event node per topic: the level a walk starts from."""
        key = episode.topic
        if key not in self._periods:
            node = make_node(
                region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
                vec=embed((key,)), keys=(key,), span=(episode.tick, episode.tick),
                text_ref=f"the {key} stretch",
            )
            self._periods[key] = self.tree.insert(node, tick=episode.tick)
        return self._periods[key]

    def perceive(self, episode: Episode) -> None:
        self.tree.insert(
            _episode_node(episode), parent=self._period_node(episode), tick=episode.tick
        )

    def on_tick(self, tick: int) -> None:
        # On the clock, and on pressure. A store that reclaims only on a
        # timer overshoots for as long as the timer has left to run.
        due = tick % self.consolidate_every == 0
        if due or self.tree.store_bytes() > self.budgets.store_bytes:
            self.consolidation.run(self.tree, tick=tick, window=self.consolidate_every * 4)

    def recall(self, question: Question) -> Outcome:
        self.tree.reset_comparisons()
        walk = RecallMachine(
            self.tree,
            ops_budget=self.budgets.recall_ops,
            context_budget_tokens=self.budgets.context_tokens,
            beam=self.beam,
            tokens_of=self.tokens_of,
        )
        resident = self.core.addresses()
        walk.begin(
            topics=question.cue_topics, entities=question.cue_entities,
            tick=question.tick, resident=resident,
        )
        result = walk.run_fast_path(max_materialized=64)
        # Kept so a caller can read what the walk cost in working memory,
        # which is the binding constraint on a microcontroller.
        self.machine = walk
        return Outcome(
            nodes=result.nodes,
            tokens=result.total_tokens,
            comparisons=self.tree.comparisons,
            ops=result.path.ops_used,
        )

    def store_bytes(self) -> int:
        return self.tree.store_bytes()

    def stats(self) -> dict:
        return {
            "nodes": len(self.tree),
            "dropped": 0,
            "grades": self.tree.grade_histogram(),
            "emerged_traits": len(self.core.emerged()),
        }


REGISTRY = {p.name: p for p in (B0Full, B1Window, B2Rag, B2cCompressed, STree)}
