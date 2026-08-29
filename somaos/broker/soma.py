"""The one class an application talks to.

Everything under this module is the memory system; this is the handle on
it. Before it existed, using SomaOS meant assembling five parts in the
right order -- tree, dilution engine, CORE, triggers, consolidation --
and knowing which of them to call on a tick and which on a question. The
only place that assembly existed was ``bench/policies/life.py``, which is
a benchmark policy: an application would have had to copy it, and would
have inherited the benchmark's assumptions along with it.

Six verbs::

    soma = SomaOS(store_budget_bytes=1_000_000)

    soma.remember(Observation.of("alice", "coffee", tick=t))
    soma.intend(Intent(id="standup", kind="time", due_tick=t + 30))
    fired = soma.tick(t)                       # timers, cues, consolidation
    recollection = soma.recall(Cue.about("coffee", tick=t))
    soma.save("agent.somaos")
    soma = SomaOS.load("agent.somaos")

The three budgets stay explicit rather than being given defaults that
would quietly decide the agent's character:

    store_budget_bytes    how much it may remember, and how sharply --
                          "brain size". Below roughly a kilobyte per
                          memory, detail starts fading; the concepts do
                          not (plans/05_EMBEDDED_TARGET.md).
    context_budget_tokens how much may be in front of the model at once.
    recall_ops_budget     how hard it may try to remember one thing.

Two seams are constructor arguments because they are the two places a
real deployment differs from these measurements: ``embedder`` decides
what "similar" means, and ``navigator`` decides who chooses where a walk
goes. Both default to the offline versions everything was measured with,
so a first run needs neither a model nor a network.
"""
from __future__ import annotations

from pathlib import Path

from somaos.broker.consolidation import ConsolidationMachine
from somaos.broker.dilution import DilutionEngine
from somaos.broker.events import (
    CueLike,
    IntentLike,
    ObservationLike,
    Recollection,
)
from somaos.broker.memory.embedding import DEFAULT_EMBEDDER, Embedder
from somaos.broker.memory.node import ArchiveLevel, MemoryNode, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.persistence import read as _read_store
from somaos.broker.persistence import save as _save_store
from somaos.broker.recall.machine import RecallMachine, structural_tokens
from somaos.broker.recall.navigator import FastPathNavigator, Navigator
from somaos.broker.regions import CoreSet, Trigger, TriggerKind, TriggerRegistry

#: Share of the store reserved for identity. CORE may never be diluted, so
#: it has to be carved out before anything competes for the rest; a tenth
#: is enough for a persona and small enough not to crowd out experience.
CORE_QUOTA_FRACTION = 10

#: Traits the default quota must always fit, however small the store.
#: A tenth of a device-sized store is one vector, and an identity of one
#: trait is not an identity -- on the very hosts where "is this still the
#: same agent" matters most, the fraction alone would have made a persona
#: impossible to seed. Four is enough for a trait, a preference, a goal
#: and a way of working; past that an application should say what it
#: needs with ``core_quota_bytes``.
MIN_CORE_TRAITS = 4

#: How often the consolidation cycle runs, in ticks. Batched rather than
#: per-observation because that is what it models: people rebuild the
#: structure of what they know while asleep, not mid-conversation.
DEFAULT_CONSOLIDATE_EVERY = 25


class SomaOS:
    """One agent's memory."""

    def __init__(
        self,
        *,
        store_budget_bytes: int,
        context_budget_tokens: int = 2048,
        recall_ops_budget: int = 32,
        embedder: Embedder = DEFAULT_EMBEDDER,
        navigator: Navigator | None = None,
        beam: int = 4,
        consolidate_every: int = DEFAULT_CONSOLIDATE_EVERY,
        core_quota_bytes: int | None = None,
        tokens_of=structural_tokens,
        _restored: dict | None = None,
    ) -> None:
        if store_budget_bytes <= 0:
            raise ValueError("store_budget_bytes must be positive")
        if context_budget_tokens <= 0:
            raise ValueError("context_budget_tokens must be positive")
        if recall_ops_budget <= 0:
            raise ValueError("recall_ops_budget must be positive")

        self.store_budget_bytes = int(store_budget_bytes)
        self.context_budget_tokens = int(context_budget_tokens)
        self.recall_ops_budget = int(recall_ops_budget)
        self.embedder = embedder
        self.navigator = navigator or FastPathNavigator()
        self.beam = beam
        self.consolidate_every = consolidate_every
        self.tokens_of = tokens_of

        restored = _restored or {}
        self.tree: MemoryTree = restored.get("tree") or MemoryTree(beam=beam)
        self.core: CoreSet = restored.get("core") or CoreSet(
            quota_bytes=int(core_quota_bytes) if core_quota_bytes is not None
            else self.default_core_quota(store_budget_bytes, embedder.dim)
        )
        self.triggers: TriggerRegistry = restored.get("triggers") or TriggerRegistry()
        self.dilution = DilutionEngine(store_budget_bytes=self.store_budget_bytes)
        self.consolidation = ConsolidationMachine(
            dilution=self.dilution, core=self.core
        )

        self.tick_count: int = restored.get("tick", 0)
        #: One general-event node per topic. The level a walk starts from,
        #: so a new topic needs somewhere to start before its first
        #: memory exists.
        self._topics: dict[str, str] = {}
        self._reindex_topics()

    @staticmethod
    def default_core_quota(store_budget_bytes: int, dim: int) -> int:
        """Bytes reserved for identity when the caller does not say.

        A share of the store, but never fewer than a handful of traits:
        on a device-sized store the share alone comes to a single vector,
        and an agent with one trait cannot meaningfully be "the same
        agent" across a reload. Exposed rather than inlined so an
        application can ask what it will get before it starts seeding.
        """
        per_trait = dim * 4
        return max(per_trait * MIN_CORE_TRAITS,
                   store_budget_bytes // CORE_QUOTA_FRACTION)

    def identity_headroom(self) -> int:
        """Bytes of identity still available. Zero means no more traits fit.

        Seeding past the quota raises rather than diluting the persona
        (N-06), so an application that seeds from configuration needs a
        way to check first instead of catching.
        """
        return max(0, self.core.quota_bytes - self.core.used_bytes(self.tree))

    # ------------------------------------------------------------ internals

    def _reindex_topics(self) -> None:
        """Rebuild the topic index from the tree.

        Needed after a load: the index is derived state, and rebuilding it
        is both cheaper and safer than persisting it, because a persisted
        index could disagree with the tree it indexes.
        """
        self._topics = {}
        for addr in self.tree.region_members(Region.ARCHIVE):
            node = self.tree.get(addr)
            if node is not None and node.level == int(ArchiveLevel.GENERAL_EVENT):
                for key in node.keys:
                    self._topics.setdefault(key, addr)

    def _topic_node(self, topic: str, tick: int) -> str:
        known = self._topics.get(topic)
        if known is not None and known in self.tree:
            return known
        node = make_node(
            region=Region.ARCHIVE,
            level=int(ArchiveLevel.GENERAL_EVENT),
            vec=self.embedder.encode((topic,)),
            keys=(topic,),
            span=(tick, tick),
            text_ref=f"the {topic} stretch",
        )
        addr = self.tree.insert(node, tick=tick)
        self._topics[topic] = addr
        return addr

    # ------------------------------------------------------------ verbs

    def remember(self, observation: ObservationLike) -> str:
        """Store one thing that happened. Returns its address.

        The address is stable, and permanent in the sense that matters:
        it will always resolve to something (N-01). What it resolves *to*
        may grow blurrier if the store comes under pressure, and may
        eventually be the group the memory belongs to rather than the
        memory itself -- but never to nothing.
        """
        keys = tuple(observation.keys)
        if not keys:
            raise ValueError("an observation with no keys cannot be remembered")
        tick = int(observation.tick)
        topic = getattr(observation, "topic", "") or keys[0]

        parent = self._topic_node(topic, tick)
        node = make_node(
            region=Region.ARCHIVE,
            level=int(ArchiveLevel.SPECIFIC_EVENT),
            vec=self.embedder.encode(keys),
            keys=keys,
            span=(tick, tick),
            text_ref=getattr(observation, "text_ref", "") or f"t{tick}: {' '.join(keys)}",
        )
        return self.tree.insert(node, parent=parent, tick=tick)

    def intend(self, intent: IntentLike) -> str:
        """Arm something to be done later. Costs nothing per tick to hold."""
        if intent.kind == "time":
            trigger = Trigger(
                id=intent.id, kind=TriggerKind.TIME,
                due_tick=intent.due_tick, action=intent.action,
            )
        else:
            trigger = Trigger(
                id=intent.id, kind=TriggerKind.EVENT,
                cue=intent.cue, action=intent.action,
            )
        return self.triggers.arm(trigger)

    def tick(self, tick: int, *, cues: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Advance time. Returns the intentions that came due.

        Also where consolidation runs -- on the clock, and immediately if
        the store is over budget, because a store that only reclaims on a
        timer overshoots for as long as the timer has left to run.
        """
        self.tick_count = max(self.tick_count, int(tick))

        fired = [t.id for t in self.triggers.on_tick(tick)]
        for cue in cues:
            fired.extend(t.id for t in self.triggers.on_event(cue, tick=tick))
        for trigger_id in fired:
            self.triggers.complete(trigger_id, tick=tick)

        due = tick % self.consolidate_every == 0
        if due or self.tree.store_bytes() > self.store_budget_bytes:
            self.consolidation.run(
                self.tree, tick=tick, window=self.consolidate_every * 4
            )
        return tuple(fired)

    def recall(self, cue: CueLike, *, max_memories: int = 8) -> Recollection:
        """Try to remember. Always returns something; never raises on a miss.

        What comes back is keys and shadow text, not vectors: an
        application should be reading what the agent remembers, not the
        geometry it remembers it in.
        """
        self.tree.reset_comparisons()
        machine = RecallMachine(
            self.tree,
            ops_budget=self.recall_ops_budget,
            context_budget_tokens=self.context_budget_tokens,
            beam=self.beam,
            tokens_of=self.tokens_of,
        )
        machine.begin(
            topics=tuple(cue.cue_topics),
            entities=tuple(cue.cue_entities),
            tick=int(cue.tick),
            resident=self.core.addresses(),
        )
        result = self.navigator.drive(machine, max_materialized=max_memories)
        return Recollection(
            keys=tuple(node.keys for node in result.nodes),
            text_refs=tuple(node.text_ref for node in result.nodes),
            tokens=result.total_tokens,
            comparisons=self.tree.comparisons,
            ops=result.path.ops_used,
            path=result.path.to_jsonable(),
        )

    def seed_identity(self, keys: tuple[str, ...], *, level, text_ref: str = "") -> str:
        """Give the agent a trait it did not have to earn.

        Seeded identity is a premise: it is never demoted and never
        diluted. Traits the agent works out about itself arrive on their
        own through consolidation.
        """
        node = make_node(
            region=Region.CORE, level=int(level),
            vec=self.embedder.encode(tuple(keys)),
            keys=tuple(keys), span=(self.tick_count, self.tick_count),
            text_ref=text_ref or " ".join(keys),
        )
        return self.core.seed(self.tree, node, level)

    # ------------------------------------------------------------ state

    def save(self, path: str | Path, *, keep_text: bool = True, meta: dict | None = None) -> int:
        """Write this agent's memory to disk. Atomic; returns records written."""
        return _save_store(
            path, tree=self.tree, core=self.core, triggers=self.triggers,
            tick=self.tick_count, keep_text=keep_text,
            meta={
                "store_budget_bytes": self.store_budget_bytes,
                "context_budget_tokens": self.context_budget_tokens,
                "recall_ops_budget": self.recall_ops_budget,
                "embedder": repr(self.embedder),
                "dim": self.embedder.dim,
                "core_quota_bytes": self.core.quota_bytes,
                **(meta or {}),
            },
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        embedder: Embedder = DEFAULT_EMBEDDER,
        navigator: Navigator | None = None,
        store_budget_bytes: int | None = None,
        context_budget_tokens: int | None = None,
        recall_ops_budget: int | None = None,
        tokens_of=structural_tokens,
    ) -> SomaOS:
        """Read an agent's memory back.

        Budgets come from the file unless overridden, so reloading an
        agent does not silently resize its brain. The embedder's
        dimensionality is checked against the stored vectors: loading a
        store written by a different encoder would leave every address
        pointing at a vector it can no longer be compared against, and
        failing loudly is the only useful answer.
        """
        store = _read_store(path, dim=embedder.dim if store_budget_bytes is None else None)
        meta = store.meta or {}

        stored_dim = meta.get("dim")
        if stored_dim is not None and int(stored_dim) != embedder.dim:
            raise ValueError(
                f"this store was written with a {stored_dim}-dimensional "
                f"embedder and is being opened with a {embedder.dim}-dimensional "
                "one; its addresses would no longer resolve against comparable "
                "vectors. Re-embed the store or open it with the original encoder."
            )

        soma = cls(
            store_budget_bytes=int(
                store_budget_bytes or meta.get("store_budget_bytes") or 1_000_000
            ),
            context_budget_tokens=int(
                context_budget_tokens or meta.get("context_budget_tokens") or 2048
            ),
            recall_ops_budget=int(
                recall_ops_budget or meta.get("recall_ops_budget") or 32
            ),
            embedder=embedder,
            navigator=navigator,
            beam=store.tree.beam,
            tokens_of=tokens_of,
            _restored={
                "tree": store.tree,
                "core": store.core,
                "triggers": store.triggers,
                "tick": store.tick,
            },
        )
        return soma

    # ------------------------------------------------------------ reporting

    def stats(self) -> dict:
        """What the memory currently looks like. Structured, never printed."""
        return {
            "tick": self.tick_count,
            "memories": len(self.tree),
            "store_bytes": self.tree.store_bytes(),
            "store_budget_bytes": self.store_budget_bytes,
            "store_used_fraction": round(
                self.tree.store_bytes() / self.store_budget_bytes, 4
            ),
            # Non-zero means enforcement could not get the store under its
            # budget -- everything dilutable is already at its floor. It is
            # reported rather than swallowed because the honest answer is
            # "this agent needs a bigger store", and an application can
            # only act on that if it can see it.
            "over_budget_bytes": self.dilution.shortfall(self.tree),
            "grades": self.tree.grade_histogram(),
            "identity": len(self.core.addresses()),
            "skills": len(self.tree.region_members(Region.SKILL)),
            "groupings": sum(
                1 for a in self.tree.region_members(Region.ARCHIVE)
                if (n := self.tree.get(a)) is not None
                and n.level == int(ArchiveLevel.GENERAL_EVENT)
            ),
            "forwarded_addresses": len(self.tree.alias.links),
            "dim": self.embedder.dim,
        }

    def __len__(self) -> int:
        return len(self.tree)

    def __repr__(self) -> str:
        return (
            f"SomaOS(memories={len(self.tree)}, tick={self.tick_count}, "
            f"store={self.tree.store_bytes()}/{self.store_budget_bytes}B)"
        )
