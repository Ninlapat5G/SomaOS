"""Writing memory to a file, and reading it back exactly.

Until now the store lived in RAM and died with the process, which for a
system whose whole claim is "never deletes a memory" was the one gap that
made the claim untestable outside a single run.

The format is JSON Lines with a typed first line:

    {"kind": "header",  "format": "somaos-memory", "version": 1, ...}
    {"kind": "node",    "addr": "...", "vec": "<base64>", ...}   x N
    {"kind": "alias",   "old": "...", "new": "...", ...}         x M
    {"kind": "counter", "addr": "...", "n": 3}
    {"kind": "core",    "addr": "...", "level": 0, ...}
    {"kind": "trigger", "id": "...", ...}

Chosen for three reasons. It streams, so a large store never has to be
held twice in memory. It is greppable, so "which memory faded into which"
stays answerable with a text editor -- that history is a requirement, not
a debugging nicety. And it carries a version, so a format change is a
migration rather than a silent misread.

**This is the host format, not the device format.** The microcontroller
work in plans/05_EMBEDDED_TARGET.md needs records at fixed offsets that
can be read one node at a time out of flash; JSON parsing on an MCU is
the wrong shape entirely. Keeping them separate is deliberate: this one
optimises for being inspectable, that one for being seekable.

Vectors are base64 of whatever the node's grade actually stores -- float32
at D0, int8 at D1, one packed bit per dimension at D2 -- so the file
agrees with the byte budget the store is sized in. Round-tripping is
exact, dtype included: a diluted node comes back as int8, not as float32
that happens to hold the same numbers.

**The file is bigger than the store it holds, and increasingly so as the
store is diluted.** At full precision it runs about 2x; at sign bits,
about 13x. Nothing is wrong: base64 costs a third, and once a vector is
32 bytes the addresses dominate -- a node carries its own 71-character
address plus its parent's and its children's. That is the price of a
format you can grep, and it is the right trade for a host. It is also
precisely why the device format has to be a different one.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import IO, Iterator

from somaos.broker.memory.tree import MemoryTree
from somaos.broker.regions.core import CoreSet
from somaos.broker.regions.trigger import TriggerRegistry

FORMAT = "somaos-memory"
VERSION = 1

#: Every record type the format defines. Also the write-side guard: a
#: payload that carries its own "kind" and gets splatted would produce a
#: record outside this set, which is how the trigger rows first went out
#: labelled "time" and came back unreadable.
_RECORD_KINDS = frozenset({
    "header", "node", "alias", "counter",
    "core_meta", "core", "trigger_meta", "trigger",
})


class PersistenceError(RuntimeError):
    """The file is not a memory store, or not one this version can read."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ------------------------------------------------------------------ writing

def _records(
    *,
    tree: MemoryTree,
    core: CoreSet | None,
    triggers: TriggerRegistry | None,
    tick: int,
    meta: dict | None,
    keep_text: bool,
) -> Iterator[dict]:
    snap = tree.snapshot()

    yield {
        "kind": "header",
        "format": FORMAT,
        "version": VERSION,
        "tick": tick,
        "dim": snap["nodes"][0]["dim"] if snap["nodes"] else 0,
        "beam": snap["beam"],
        "decay_per_tick": snap["decay_per_tick"],
        "comparisons": snap["comparisons"],
        "node_count": len(snap["nodes"]),
        "alias_count": len(snap["aliases"]),
        "keep_text": keep_text,
        "has_core": core is not None,
        "has_triggers": triggers is not None,
        "meta": meta or {},
    }

    children_order = snap["children_order"]
    for row in snap["nodes"]:
        out = dict(row)
        out["kind"] = "node"
        out["vec"] = _b64(row["vec"])
        if not keep_text:
            out["text_ref"] = ""
        order = children_order.get(row["addr"])
        if order is not None:
            out["children_order"] = order
        yield out

    for link in snap["aliases"]:
        yield {"kind": "alias", **link}

    for addr, count in snap["counters"].items():
        yield {"kind": "counter", "addr": addr, "n": count}

    if core is not None:
        csnap = core.snapshot()
        yield {"kind": "core_meta", "quota_bytes": csnap["quota_bytes"],
               "tokens_per_node": csnap["tokens_per_node"]}
        for row in csnap["members"]:
            yield {"kind": "core", **row}

    if triggers is not None:
        tsnap = triggers.snapshot()
        yield {"kind": "trigger_meta", "ops_used": tsnap["ops_used"]}
        for row in tsnap["triggers"]:
            # Nested, not splatted. A trigger carries its own "kind"
            # ("time" / "event"), which splatting would write straight
            # over the record's discriminator -- the file then reads back
            # as an unknown record type. Any payload with a field named
            # "kind" belongs in its own object.
            yield {"kind": "trigger", "trigger": row}


def dump(
    stream: IO[str],
    *,
    tree: MemoryTree,
    core: CoreSet | None = None,
    triggers: TriggerRegistry | None = None,
    tick: int = 0,
    meta: dict | None = None,
    keep_text: bool = True,
) -> int:
    """Write a store to an open text stream. Returns records written.

    ``keep_text`` off drops every ``text_ref``. Supported because
    invariant V1 says stripping the shadow text must leave retrieval
    unchanged, and the only way that stays checkable is if a store can
    actually be written without it -- and because the text is the one
    part of a memory that might carry something private.
    """
    written = 0
    for record in _records(tree=tree, core=core, triggers=triggers,
                           tick=tick, meta=meta, keep_text=keep_text):
        if record.get("kind") not in _RECORD_KINDS:
            raise PersistenceError(
                f"record discriminator was overwritten: kind={record.get('kind')!r}. "
                "A payload field named 'kind' has been splatted over it; nest "
                "that payload instead of spreading it."
            )
        stream.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
        stream.write("\n")
        written += 1
    return written


def save(
    path: str | Path,
    *,
    tree: MemoryTree,
    core: CoreSet | None = None,
    triggers: TriggerRegistry | None = None,
    tick: int = 0,
    meta: dict | None = None,
    keep_text: bool = True,
) -> int:
    """Write a store to ``path``, atomically.

    Through a temporary file and a rename, because the alternative is
    that a crash midway through leaves a half-written store where a whole
    one used to be. For a system that promises not to lose memories, the
    write is exactly where that promise is easiest to break.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".partial")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            written = dump(handle, tree=tree, core=core, triggers=triggers,
                           tick=tick, meta=meta, keep_text=keep_text)
            handle.flush()
        tmp.replace(path)
        return written
    finally:
        if tmp.exists():
            tmp.unlink()


# ------------------------------------------------------------------ reading

class Store:
    """What came back off disk."""

    __slots__ = ("tree", "core", "triggers", "tick", "meta", "header")

    def __init__(self, *, tree: MemoryTree, core: CoreSet | None,
                 triggers: TriggerRegistry | None, tick: int,
                 meta: dict, header: dict) -> None:
        self.tree = tree
        self.core = core
        self.triggers = triggers
        self.tick = tick
        self.meta = meta
        self.header = header

    def __repr__(self) -> str:
        return (f"Store(nodes={len(self.tree)}, tick={self.tick}, "
                f"core={'yes' if self.core else 'no'})")


def load(stream: IO[str], *, dim: int | None = None) -> Store:
    """Read a store back. Raises rather than returning something partial."""
    lines = iter(stream)
    try:
        header = json.loads(next(lines))
    except StopIteration as exc:
        raise PersistenceError("empty file: no header record") from exc
    except json.JSONDecodeError as exc:
        raise PersistenceError(f"first line is not JSON: {exc}") from exc

    if header.get("kind") != "header" or header.get("format") != FORMAT:
        raise PersistenceError(
            f"not a {FORMAT} file (first record says "
            f"format={header.get('format')!r}, kind={header.get('kind')!r})"
        )
    if header.get("version") != VERSION:
        raise PersistenceError(
            f"file is format version {header.get('version')}, this build "
            f"reads version {VERSION}; migrate it rather than guessing"
        )

    snap: dict = {
        "beam": header.get("beam", 4),
        "decay_per_tick": header.get("decay_per_tick", 0.999),
        "comparisons": header.get("comparisons", 0),
        "nodes": [], "aliases": [], "counters": {}, "children_order": {},
    }
    core_meta: dict | None = None
    core_members: list[dict] = []
    trigger_meta: dict | None = None
    trigger_rows: list[dict] = []

    for number, line in enumerate(lines, start=2):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PersistenceError(f"line {number} is not JSON: {exc}") from exc

        kind = record.get("kind")
        if kind == "node":
            record["vec"] = _unb64(record["vec"])
            order = record.pop("children_order", None)
            if order is not None:
                snap["children_order"][record["addr"]] = order
            snap["nodes"].append(record)
        elif kind == "alias":
            snap["aliases"].append(record)
        elif kind == "counter":
            snap["counters"][record["addr"]] = record["n"]
        elif kind == "core_meta":
            core_meta = record
        elif kind == "core":
            core_members.append(record)
        elif kind == "trigger_meta":
            trigger_meta = record
        elif kind == "trigger":
            trigger_rows.append(record["trigger"])
        else:
            raise PersistenceError(f"line {number}: unknown record kind {kind!r}")

    found = len(snap["nodes"])
    expected = header.get("node_count")
    if expected is not None and found != expected:
        raise PersistenceError(
            f"header promises {expected} memories but the file holds {found}; "
            "refusing to load a store that has been truncated"
        )

    tree = MemoryTree.restore(snap, dim=dim)

    core = None
    if core_meta is not None:
        core = CoreSet.restore({
            "quota_bytes": core_meta["quota_bytes"],
            "tokens_per_node": core_meta.get("tokens_per_node", 32),
            "members": core_members,
        })

    triggers = None
    if trigger_meta is not None:
        triggers = TriggerRegistry.restore({
            "ops_used": trigger_meta.get("ops_used", 0),
            "triggers": trigger_rows,
        })

    return Store(tree=tree, core=core, triggers=triggers,
                 tick=int(header.get("tick", 0)),
                 meta=header.get("meta", {}), header=header)


def read(path: str | Path, *, dim: int | None = None) -> Store:
    """Read a store from ``path``."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return load(handle, dim=dim)
