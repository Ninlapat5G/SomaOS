import json

import pytest

from somaos.broker.types import (
    BudgetExceeded,
    ContextBundle,
    MemoryItem,
    Query,
    QueryView,
    Tier,
    to_view,
)
from somaos.util.hashing import canonical_json


def make_item(id_, tokens=10, tick=0):
    return MemoryItem(
        id=id_, kind="episodic", tokens=tokens, created_tick=tick,
        topics=("a", "b"), entities=("e1",), surprise=0.5, novelty=0.0,
    )


def test_bundle_hash_equal_for_equal_input():
    items = (make_item("i1"), make_item("i2"))
    b1 = ContextBundle(query_id="q1", tick=5, budget_tokens=100, items=items)
    b2 = ContextBundle(query_id="q1", tick=5, budget_tokens=100, items=items)
    assert b1.bundle_hash == b2.bundle_hash


def test_bundle_hash_differs_on_order():
    i1, i2 = make_item("i1"), make_item("i2")
    b1 = ContextBundle(query_id="q1", tick=5, budget_tokens=100, items=(i1, i2))
    b2 = ContextBundle(query_id="q1", tick=5, budget_tokens=100, items=(i2, i1))
    assert b1.bundle_hash != b2.bundle_hash


def test_bundle_hash_stable_across_hashseed():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = (
        "from somaos.broker.types import MemoryItem, ContextBundle\n"
        "i = MemoryItem(id='i1', kind='episodic', tokens=10, created_tick=0, "
        "topics=('a','b'), entities=('e1',), surprise=0.5, novelty=0.0)\n"
        "b = ContextBundle(query_id='q1', tick=5, budget_tokens=100, items=(i,))\n"
        "print(b.bundle_hash)\n"
    )
    import os
    env0 = dict(os.environ, PYTHONHASHSEED="0")
    env1 = dict(os.environ, PYTHONHASHSEED="1")
    out0 = subprocess.run([sys.executable, "-c", code], env=env0, capture_output=True, text=True, cwd=str(root))
    out1 = subprocess.run([sys.executable, "-c", code], env=env1, capture_output=True, text=True, cwd=str(root))
    assert out0.stdout.strip() == out1.stdout.strip()
    assert out0.stdout.strip() != ""


def test_validate_raises_over_budget():
    items = (make_item("i1", tokens=60), make_item("i2", tokens=60))
    b = ContextBundle(query_id="q1", tick=0, budget_tokens=100, items=items)
    with pytest.raises(BudgetExceeded):
        b.validate()


def test_validate_ignores_budget_flag():
    items = (make_item("i1", tokens=60), make_item("i2", tokens=60))
    b = ContextBundle(query_id="q1", tick=0, budget_tokens=100, items=items)
    b.validate(ignores_budget=True)  # must not raise


def test_memory_item_hashable_and_dict_key():
    i1 = make_item("i1")
    d = {i1: "value"}
    assert d[i1] == "value"


def test_memory_item_json_roundtrip_values_equal():
    i1 = make_item("i1")
    encoded = canonical_json(i1.to_jsonable())
    decoded = json.loads(encoded)
    assert decoded["id"] == i1.id
    assert decoded["tokens"] == i1.tokens
    assert tuple(decoded["topics"]) == i1.topics


def test_query_view_has_no_answer_key():
    q = Query(id="q1", tick=0, topics=("a",), entities=(), required_item_ids=frozenset({"i1"}))
    view = to_view(q)
    assert not hasattr(view, "required_item_ids")
    assert view.id == q.id and view.tick == q.tick


def test_query_is_hashable():
    q = Query(id="q1", tick=0, topics=("a",), entities=(), required_item_ids=frozenset({"i1"}))
    {q: 1}  # must not raise


def test_bundle_tokens_sum():
    items = (make_item("i1", tokens=10), make_item("i2", tokens=20))
    b = ContextBundle(query_id="q1", tick=0, budget_tokens=100, items=items)
    assert b.tokens == 30


def test_tier_ordering():
    assert Tier.WORKING < Tier.WARM < Tier.COLD
