"""A synthetic life with ground truth, and questions at four levels (N-12).

The old generator asked one kind of question: "give me the exact item that
last recorded fact F". That is a fine test of episodic lookup and a useless
test of everything this project is about -- a memory that kept the gist and
lost the detail scored zero, identical to a memory that was never stored.
The thing the design exists to do was worth nothing on the scoreboard.

So this world is built to be asked four different kinds of question, and
it keeps the ground truth to mark all four:

    DETAIL   which exact thing happened that morning
    GIST     what was that stretch of time about
    HABIT    what does this person usually do
    TRIGGER  did the intention fire when it should have

Only DETAIL is what the old harness measured. GIST is the one the capacity
curve turns on: it should survive long after DETAIL has gone.

The world never imports a policy. Everything here is generated from a
seeded RNG and is identical for every policy under test, which is what
makes the comparison a comparison.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from somaos.util.rng import make_rng

QueryKind = Literal["detail", "gist", "habit", "trigger"]


@dataclass(frozen=True, slots=True)
class WorldConfig:
    n_ticks: int = 400
    n_entities: int = 8
    n_topics: int = 10
    #: Recurring behaviours -- an entity doing the same thing in the same
    #: context. These are what HABIT questions are about, and what
    #: consolidation is supposed to notice on its own.
    n_routines: int = 4
    routine_period: int = 5
    #: One-off events per tick. The bulk of experience, and what DETAIL
    #: questions are about.
    episodes_per_tick: int = 2
    #: Chapters. Each is a stretch of ticks where one topic dominates, so
    #: "what was that period about" has an answer.
    n_periods: int = 6
    n_intentions: int = 6
    tokens_min: int = 8
    tokens_max: int = 40
    #: Fraction of queries of each kind. Must sum to 1.
    mix: tuple[float, float, float] = (0.4, 0.35, 0.25)  # detail, gist, habit
    queries_per_10_ticks: int = 3
    seed_root: str = "dev-01"


@dataclass(frozen=True, slots=True)
class Episode:
    """One thing that happened. The unit a policy perceives."""

    id: str
    tick: int
    entity: str
    topic: str
    keys: tuple[str, ...]
    tokens: int
    period: int
    routine: int | None = None

    @property
    def is_routine(self) -> bool:
        return self.routine is not None


@dataclass(frozen=True, slots=True)
class Period:
    """A stretch of time with a dominant topic -- a chapter."""

    id: int
    topic: str
    first_tick: int
    last_tick: int


@dataclass(frozen=True, slots=True)
class Intention:
    """Something the agent means to do later."""

    id: str
    kind: Literal["time", "event"]
    armed_tick: int
    due_tick: int | None = None
    cue: str | None = None
    action: str = "act"


@dataclass(frozen=True, slots=True)
class Question:
    """What a policy is asked, and what counts as an answer.

    The cue fields are all a policy ever sees. Everything below the line is
    ground truth held by the bench: a policy that could read it would be
    marking its own homework, which is the failure the previous harness
    took two rounds to notice.
    """

    id: str
    tick: int
    kind: QueryKind
    cue_topics: tuple[str, ...]
    cue_entities: tuple[str, ...]

    # -- ground truth, bench only ------------------------------------
    episode_id: str | None = None          # DETAIL: this exact episode
    episode_keys: tuple[str, ...] = ()     # DETAIL: what it was, for fidelity
    period_topic: str | None = None        # GIST: the chapter's subject
    period_span: tuple[int, int] = (0, 0)  # GIST: when it ran
    habit_keys: tuple[str, ...] = ()       # HABIT: the routine's signature
    intention_id: str | None = None        # TRIGGER: which intention


@dataclass(frozen=True, slots=True)
class LifeTrace:
    config: WorldConfig
    episodes: tuple[Episode, ...]
    periods: tuple[Period, ...]
    intentions: tuple[Intention, ...]
    questions: tuple[Question, ...]

    @property
    def tokens_by_id(self) -> dict[str, int]:
        """Token cost per episode, from the world rather than the policy.

        The previous harness learned this the hard way: a policy allowed to
        price its own output will eventually price it favourably.
        """
        return {e.id: e.tokens for e in self.episodes}

    @property
    def keys_by_id(self) -> dict[str, tuple[str, ...]]:
        return {e.id: e.keys for e in self.episodes}

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for q in self.questions:
            kinds[q.kind] = kinds.get(q.kind, 0) + 1
        return {
            "ticks": self.config.n_ticks,
            "episodes": len(self.episodes),
            "routine_episodes": sum(1 for e in self.episodes if e.is_routine),
            "periods": len(self.periods),
            "intentions": len(self.intentions),
            "questions": len(self.questions),
            "questions_by_kind": kinds,
        }


def generate(config: WorldConfig) -> LifeTrace:
    rng = make_rng(config.seed_root, "lifeworld")
    topics = tuple(f"topic{i}" for i in range(config.n_topics))
    entities = tuple(f"person{i}" for i in range(config.n_entities))

    periods = _make_periods(config, topics)
    routines = _make_routines(config, rng, topics, entities)
    episodes = _make_episodes(config, rng, topics, entities, periods, routines)
    intentions = _make_intentions(config, rng, topics)
    questions = _make_questions(config, rng, episodes, periods, routines, intentions)

    return LifeTrace(
        config=config, episodes=tuple(episodes), periods=tuple(periods),
        intentions=tuple(intentions), questions=tuple(questions),
    )


def _make_periods(config: WorldConfig, topics) -> list[Period]:
    """Chapters of equal length, each with its own dominant topic.

    Equal length on purpose: a GIST question should not be easier for one
    chapter than another, or the score would measure chapter size.
    """
    span = max(1, config.n_ticks // config.n_periods)
    return [
        Period(id=i, topic=topics[i % len(topics)],
               first_tick=i * span, last_tick=min(config.n_ticks, (i + 1) * span) - 1)
        for i in range(config.n_periods)
    ]


def _make_routines(config, rng, topics, entities) -> list[dict]:
    """Recurring behaviours: one entity, one context, done over and over.

    Each carries two extra keys so a routine has a signature distinct from
    its topic -- otherwise "what does this person usually do" would be
    answerable from the topic alone, and consolidation would have nothing
    to find that a simple index could not.
    """
    contexts = ("morning", "evening", "commute", "desk", "kitchen", "outdoors")
    actions = ("coffee", "reading", "calls", "walking", "cooking", "notes")
    out = []
    for i in range(config.n_routines):
        out.append({
            "id": i,
            "entity": entities[i % len(entities)],
            "topic": topics[i % len(topics)],
            "keys": (contexts[i % len(contexts)], actions[i % len(actions)]),
            "offset": rng.randrange(config.routine_period),
        })
    return out


def _period_of(periods: list[Period], tick: int) -> Period:
    for period in periods:
        if period.first_tick <= tick <= period.last_tick:
            return period
    return periods[-1]


def _make_episodes(config, rng, topics, entities, periods, routines) -> list[Episode]:
    episodes: list[Episode] = []
    counter = 0
    for tick in range(config.n_ticks):
        period = _period_of(periods, tick)

        for routine in routines:
            if tick % config.routine_period != routine["offset"]:
                continue
            counter += 1
            # Deliberately identical content each time it recurs. Content
            # addressing collapses that to one node with a rising
            # occurrence count, which is what makes a routine legible as a
            # routine rather than as many unrelated events.
            episodes.append(Episode(
                id=f"ep{counter}", tick=tick, entity=routine["entity"],
                topic=routine["topic"],
                keys=(routine["topic"], routine["entity"], *routine["keys"]),
                tokens=rng.randint(config.tokens_min, config.tokens_max),
                period=period.id, routine=routine["id"],
            ))

        for _ in range(config.episodes_per_tick):
            counter += 1
            # Most of what happens belongs to the chapter's topic; the rest
            # is noise, so a chapter is dominant without being pure.
            topic = period.topic if rng.random() < 0.7 else rng.choice(topics)
            entity = rng.choice(entities)
            episodes.append(Episode(
                id=f"ep{counter}", tick=tick, entity=entity, topic=topic,
                keys=(topic, entity, f"ep{counter}"),
                tokens=rng.randint(config.tokens_min, config.tokens_max),
                period=period.id, routine=None,
            ))
    return episodes


def _make_intentions(config, rng, topics) -> list[Intention]:
    out = []
    for i in range(config.n_intentions):
        armed = rng.randrange(max(1, config.n_ticks // 2))
        if i % 2 == 0:
            out.append(Intention(
                id=f"int{i}", kind="time", armed_tick=armed,
                due_tick=armed + rng.randrange(10, max(11, config.n_ticks // 4)),
            ))
        else:
            out.append(Intention(
                id=f"int{i}", kind="event", armed_tick=armed,
                cue=rng.choice(topics),
            ))
    return out


def _make_questions(config, rng, episodes, periods, routines, intentions) -> list[Question]:
    """Ask each kind of question about things that have already happened.

    A question is only ever asked after its subject exists, which sounds
    obvious and is easy to get wrong: asking about a chapter before it ends
    would score every policy on whether it can see the future.
    """
    detail_share, gist_share, _ = config.mix
    questions: list[Question] = []
    by_tick: dict[int, list[Episode]] = {}
    for episode in episodes:
        by_tick.setdefault(episode.tick, []).append(episode)

    counter = 0
    for tick in range(10, config.n_ticks, max(1, 10 // config.queries_per_10_ticks)):
        counter += 1
        roll = rng.random()
        past = [e for e in episodes if e.tick < tick and not e.is_routine]
        if not past:
            continue

        if roll < detail_share:
            target = rng.choice(past)
            questions.append(Question(
                id=f"q{counter}", tick=tick, kind="detail",
                cue_topics=(target.topic,), cue_entities=(target.entity, target.id),
                episode_id=target.id, episode_keys=target.keys,
            ))
        elif roll < detail_share + gist_share:
            done = [p for p in periods if p.last_tick < tick]
            if not done:
                continue
            period = rng.choice(done)
            questions.append(Question(
                id=f"q{counter}", tick=tick, kind="gist",
                # The cue names the chapter's subject but no specific
                # episode: this is "what was that about", not "which one".
                cue_topics=(period.topic,), cue_entities=(),
                period_topic=period.topic,
                period_span=(period.first_tick, period.last_tick),
            ))
        else:
            routine = rng.choice(routines)
            questions.append(Question(
                id=f"q{counter}", tick=tick, kind="habit",
                # Only the person is named. What they usually do is the
                # answer, not part of the question.
                cue_topics=(), cue_entities=(routine["entity"],),
                habit_keys=routine["keys"],
            ))

    for intention in intentions:
        questions.append(Question(
            id=f"q-{intention.id}", tick=intention.due_tick or config.n_ticks - 1,
            kind="trigger", cue_topics=(), cue_entities=(),
            intention_id=intention.id,
        ))
    return questions
