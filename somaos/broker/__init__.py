"""SomaOS memory runtime.

Everything an application needs is re-exported here::

    from somaos.broker import SomaOS, Observation, Cue, Intent

Nothing in this package imports ``somaos.bench``: the runtime does not
depend on the thing that measures it, and a test enforces that.
"""
from somaos.broker.events import (
    Cue,
    CueLike,
    Intent,
    IntentLike,
    Observation,
    ObservationLike,
    Recollection,
)
from somaos.broker.memory.embedding import (
    CallableEmbedder,
    Embedder,
    HashEmbedder,
    bytes_per_memory,
)
from somaos.broker.memory.node import ArchiveLevel, CoreLevel, Region
from somaos.broker.persistence import PersistenceError
from somaos.broker.recall.navigator import (
    CallableNavigator,
    FastPathNavigator,
    NavigationError,
    Navigator,
    describe,
)
from somaos.broker.soma import SomaOS

__all__ = [
    "SomaOS",
    # what goes in and comes out
    "Observation", "Cue", "Intent", "Recollection",
    "ObservationLike", "CueLike", "IntentLike",
    # the two seams
    "Embedder", "HashEmbedder", "CallableEmbedder", "bytes_per_memory",
    "Navigator", "FastPathNavigator", "CallableNavigator", "describe",
    # structure
    "Region", "ArchiveLevel", "CoreLevel",
    # errors worth catching by name
    "NavigationError", "PersistenceError",
]
