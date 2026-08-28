"""Marking, done by the bench against ground truth it holds itself (N-11).

Two rules shape this module, both learned expensively.

A policy never marks its own work. The store keeps a fidelity number, but
it is a conservative bound and it is the store's own estimate, so it is not
used here. The bench regenerates what a memory originally was from the
world's seed and compares, which is the only version of the number nobody
under test can influence.

And the mark is not one number. Scoring "did the exact item come back"
alone -- which is what the previous harness did -- gives a memory that kept
the gist and lost the detail the same zero as a memory that was never
stored. Detail and gist are marked separately, and the interesting result
is the gap between them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from somaos.bench.lifeworld import LifeTrace, Question
from somaos.broker.memory.node import MemoryNode
from somaos.broker.memory.vector import embed, similarity

#: How intact a memory has to be to answer "which exact one".
#: int8 lands around 0.999 and binary around 0.8, so this is the line
#: between a memory that still has its detail and one that has only its
#: shape -- which is exactly the distinction the ladder is built on.
DETAIL_THRESHOLD = 0.9


@dataclass(frozen=True, slots=True)
class QuestionResult:
    question_id: str
    kind: str
    detail: float
    gist: float
    tokens: int
    comparisons: int
    ops: int

    def to_jsonable(self) -> dict:
        return asdict(self)


def _topic_vectors(trace: LifeTrace) -> dict[str, np.ndarray]:
    topics = {e.topic for e in trace.episodes}
    return {t: embed((t,)) for t in sorted(topics)}


class Marker:
    """Marks a run. Built once per trace so topic vectors are shared."""

    def __init__(self, trace: LifeTrace) -> None:
        self.trace = trace
        self._topics = _topic_vectors(trace)

    # ------------------------------------------------------------ detail

    def detail_score(self, question: Question, nodes: tuple[MemoryNode, ...]) -> float:
        """Can the bundle say which exact thing happened?

        Carrying the episode's id is necessary and not sufficient: a node
        faded to sign bits still carries its keys, so keys alone would make
        dilution free. It also has to still resemble what it was.
        """
        if question.episode_id is None:
            return 0.0
        original = embed(question.episode_keys)
        for node in nodes:
            if question.episode_id not in node.keys:
                continue
            if similarity(original, node.vec) >= DETAIL_THRESHOLD:
                return 1.0
        return 0.0

    # ------------------------------------------------------------ gist

    def gist_score(self, question: Question, nodes: tuple[MemoryNode, ...]) -> float:
        """Can the bundle say what that stretch of time was about?

        Judged by whether a node still points at its own chapter's subject
        more than at any other, rather than by an absolute cosine. That is
        the property sign bits were measured to preserve, and an absolute
        threshold would instead measure how many keys a node happens to
        carry.
        """
        if question.period_topic is None:
            return 0.0
        first, last = question.period_span
        for node in nodes:
            if not _overlaps(node.span, first, last):
                continue
            best = max(self._topics, key=lambda t: similarity(node.vec, self._topics[t]))
            if best == question.period_topic:
                return 1.0
        return 0.0

    # ------------------------------------------------------------ habit

    def habit_score(self, question: Question, nodes: tuple[MemoryNode, ...]) -> float:
        """Can the bundle say what this person usually does?

        Answerable two ways, and both count: a consolidated habit node
        carrying the routine's signature, or enough of the routine's own
        episodes to make the pattern visible. A policy that has no notion
        of habits can still get there by remembering the instances, which
        is the honest baseline for the tree to have to beat.
        """
        if not question.habit_keys:
            return 0.0
        wanted = set(question.habit_keys)
        for node in nodes:
            if wanted <= set(node.keys):
                return 1.0
        return 0.0

    # ------------------------------------------------------------ dispatch

    def mark(
        self,
        question: Question,
        nodes: tuple[MemoryNode, ...],
        *,
        tokens: int,
        comparisons: int,
        ops: int,
    ) -> QuestionResult:
        detail = gist = 0.0
        if question.kind == "detail":
            detail = self.detail_score(question, nodes)
            # A question about one morning is also, more weakly, a question
            # about the period it sits in: getting the chapter right while
            # losing the morning is partial credit, and it is the whole
            # signature the capacity curve is looking for.
            gist = self._detail_as_gist(question, nodes)
        elif question.kind == "gist":
            gist = self.gist_score(question, nodes)
        elif question.kind == "habit":
            gist = self.habit_score(question, nodes)
        return QuestionResult(
            question_id=question.id, kind=question.kind,
            detail=detail, gist=gist,
            tokens=tokens, comparisons=comparisons, ops=ops,
        )

    def _detail_as_gist(self, question: Question, nodes) -> float:
        if not question.episode_keys:
            return 0.0
        topic = question.episode_keys[0]
        if topic not in self._topics:
            return 0.0
        for node in nodes:
            best = max(self._topics, key=lambda t: similarity(node.vec, self._topics[t]))
            if best == topic:
                return 1.0
        return 0.0


def _overlaps(span: tuple[int, int], first: int, last: int) -> bool:
    return not (span[1] < first or span[0] > last)


@dataclass
class RunScore:
    """Everything one policy run produced, aggregated by question kind."""

    policy: str
    seed_root: str
    store_budget_bytes: int
    context_budget_tokens: int
    recall_ops_budget: int
    results: list[QuestionResult]
    store_bytes_final: int = 0
    nodes_final: int = 0
    trigger_fired: int = 0
    trigger_expected: int = 0
    trigger_spurious: int = 0

    def _by(self, kind: str) -> list[QuestionResult]:
        return [r for r in self.results if r.kind == kind]

    @staticmethod
    def _mean(values) -> float:
        values = list(values)
        return float(np.mean(values)) if values else 0.0

    def to_jsonable(self) -> dict:
        detail_qs = self._by("detail")
        return {
            "policy": self.policy,
            "seed_root": self.seed_root,
            "store_budget_bytes": self.store_budget_bytes,
            "context_budget_tokens": self.context_budget_tokens,
            "recall_ops_budget": self.recall_ops_budget,
            "n_questions": len(self.results),
            # The two headline numbers, never collapsed into one.
            "detail": self._mean(r.detail for r in detail_qs),
            "gist": self._mean(
                r.gist for r in self.results if r.kind in ("detail", "gist", "habit")
            ),
            "gist_period": self._mean(r.gist for r in self._by("gist")),
            "gist_habit": self._mean(r.gist for r in self._by("habit")),
            "gist_of_detail_questions": self._mean(r.gist for r in detail_qs),
            "tokens_per_question": self._mean(
                r.tokens for r in self.results if r.kind != "trigger"
            ),
            "comparisons_per_question": self._mean(
                r.comparisons for r in self.results if r.kind != "trigger"
            ),
            "ops_per_question": self._mean(
                r.ops for r in self.results if r.kind != "trigger"
            ),
            "store_bytes_final": self.store_bytes_final,
            "nodes_final": self.nodes_final,
            "trigger_recall": (
                self.trigger_fired / self.trigger_expected
                if self.trigger_expected else 0.0
            ),
            "trigger_spurious": self.trigger_spurious,
        }
