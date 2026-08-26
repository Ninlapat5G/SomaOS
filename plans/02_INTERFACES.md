# Phase 0 — Shared Interfaces (normative)

> ทุก work package ต้อง import จากที่นี่ ห้ามนิยาม type ซ้ำ
> ถ้า signature ต้องเปลี่ยน → หยุด ถาม แล้วอัปเดตไฟล์นี้ก่อนเขียนโค้ด

---

## 1. `somaos/broker/types.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal, Protocol, Mapping, Sequence

MemoryKind = Literal["episodic", "semantic", "procedural", "prospective"]


class Tier(IntEnum):
    WORKING = 0
    WARM = 1
    COLD = 2


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """Immutable. สถานะที่เปลี่ยนได้ (access count/tier) อยู่ใน ItemStat"""
    id: str
    kind: MemoryKind
    tokens: int                      # ต้นทุนเมื่อวางใน context
    created_tick: int
    topics: tuple[str, ...]          # ground-truth tags จาก world
    entities: tuple[str, ...]
    surprise: float                  # [0,1]  ดู D-04
    novelty: float                   # [0,1]  ดู D-04
    pinned: bool = False
    recompute_cost: float = 0.0      # [0,1] normalized
    source_item_ids: tuple[str, ...] = ()   # ใช้ตอน B3 ยุบเป็น summary
    content: str = ""                # opaque; L1 ไม่ตีความ


@dataclass(slots=True)
class ItemStat:
    """mutable state ของ item ในมุมมองของ policy หนึ่งตัว"""
    last_access_tick: int
    access_count: int = 0
    tier: Tier = Tier.WARM
    admitted_tick: int = 0


@dataclass(frozen=True, slots=True)
class Query:
    id: str
    tick: int
    topics: tuple[str, ...]
    entities: tuple[str, ...]
    required_item_ids: frozenset[str]   # ground truth จาก world (§8.1)


@dataclass(frozen=True, slots=True)
class Observation:
    tick: int
    item: MemoryItem


@dataclass(frozen=True, slots=True)
class TraceEvent:
    tick: int
    kind: Literal["observe", "query"]
    observation: Observation | None = None
    query: Query | None = None


@dataclass(frozen=True, slots=True)
class Trace:
    trace_id: str            # sha256 ของ generator config
    events: tuple[TraceEvent, ...]
    n_ticks: int
    meta: Mapping[str, object]      # regime, seed, item stats


@dataclass(frozen=True, slots=True)
class EncodeDecision:
    """ผลของ fast path ตอน perceive (§6)"""
    encoded: bool                    # เก็บเป็น episode เต็มไหม
    reason: Literal["surprise_high", "novel", "low_surprise_counter", "filtered"]
    counter_delta: int = 0           # ถ้าไม่เก็บ ให้บวก observation_count


@dataclass(frozen=True, slots=True)
class ContextBundle:
    query_id: str
    tick: int
    budget_tokens: int
    items: tuple[MemoryItem, ...]    # เรียงแล้ว: static-before-dynamic (§6)

    @property
    def tokens(self) -> int: ...
    @property
    def bundle_hash(self) -> str:
        """sha256 ของ canonical json: [(id, tokens) ...] ตามลำดับจริง + budget"""
```

**Invariants ที่ต้อง assert:**
- `bundle.tokens <= bundle.budget_tokens` เสมอ (ยกเว้น B0 ที่ประกาศ `ignores_budget = True`)
- `bundle_hash` เท่ากันทุกครั้งสำหรับ input เดียวกัน ข้าม process (ห้ามใช้ `hash()`)
- item ที่ `pinned=True` ต้องอยู่ใน bundle เสมอถ้ายังมีที่ว่างพอ

---

## 2. `somaos/broker/policy.py`

```python
class MemoryPolicy(Protocol):
    name: str                     # "B0" | "B1" | "B2" | "B3" | "B4" | "S"
    ignores_budget: bool          # True เฉพาะ B0

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        """เรียกก่อนรัน trace ทุกครั้ง ต้องล้าง state ทั้งหมด"""

    def observe(self, obs: Observation) -> EncodeDecision:
        """fast path — ห้ามแตะ LLM ห้ามใช้เวลาเกิน budget ของ D-07"""

    def on_tick(self, tick: int) -> None:
        """maintenance ต่อ tick (decay, promote/demote) — optional no-op"""

    def on_query(self, q: Query) -> ContextBundle:
        """ประกอบ bundle ที่จะส่งเข้าโมเดล (ที่ L1 ใช้วัด answerability)"""

    def stats(self) -> dict[str, float]:
        """counter สะสม: llm_calls, evictions, promotions, demotions, ..."""


POLICY_REGISTRY: dict[str, type]   # ลงทะเบียนด้วย @register_policy("B1")
def build_policy(name: str, **kwargs) -> MemoryPolicy: ...
```

---

## 3. `somaos/broker/retention.py` (pure)

```python
@dataclass(frozen=True, slots=True)
class RetentionWeights:
    w_recency: float
    w_frequency: float
    w_relevance: float
    w_surprise: float
    w_novelty: float          # D-04
    w_pinned: float
    w_recompute: float

    @classmethod
    def from_json(cls, path_or_obj) -> RetentionWeights: ...
    def normalized(self) -> RetentionWeights:
        """หารด้วยผลรวม → score อยู่ใน [0,1] เทียบข้าม config ได้"""


@dataclass(frozen=True, slots=True)
class RetentionFeatures:
    recency: float      # [0,1]
    frequency: float
    relevance: float
    surprise: float
    novelty: float
    pinned: float       # 0.0 | 1.0
    recompute: float


def extract_features(
    item: MemoryItem, stat: ItemStat, *, now_tick: int,
    tau_ticks: int, goal_topics: frozenset[str], goal_entities: frozenset[str],
    max_access_count: int,
) -> RetentionFeatures: ...


def retention_score(f: RetentionFeatures, w: RetentionWeights) -> float:
    """pure. ผลลัพธ์ใน [0,1]. ห้ามมี side effect ห้ามอ่าน global"""
```

**สัญญาเชิงคณิตศาสตร์ (ต้องมี test):**
- monotone: เพิ่ม feature ใด ๆ ที่ weight > 0 → score ไม่ลด
- bounded: `0.0 <= score <= 1.0` สำหรับทุก input ที่ feature อยู่ใน [0,1]
- deterministic: เรียกซ้ำได้ผลเดิม bit-for-bit
- weight ศูนย์ → feature นั้นไม่มีผลเลย (ใช้ property test ยืนยัน)

---

## 4. `somaos/broker/workingset.py`

```python
@dataclass(frozen=True, slots=True)
class AllocationResult:
    admitted: tuple[str, ...]        # item ids ที่เข้า WORKING รอบนี้
    evicted: tuple[str, ...]
    resident: tuple[str, ...]        # working set หลัง allocate (เรียงตาม score desc)
    tokens_used: int
    churn: int                       # |admitted| + |evicted|


class WorkingSetAllocator:
    def __init__(self, *, budget_tokens: int, weights, tau_ticks: int,
                 hysteresis: float = 0.0): ...

    def allocate(self, *, now_tick: int, candidates: Sequence[tuple[MemoryItem, ItemStat]],
                 goal_topics, goal_entities) -> AllocationResult: ...

    def churn_rate(self, window: int = 32) -> float: ...
    def thrash_indicator(self, progress_rate: float) -> float:
        """churn สูง + progress ต่ำ (§5.5)"""
```

**หมายเหตุ algorithm:** knapsack ด้วย `score/tokens` ratio (greedy) + pinned บังคับเข้าก่อน
`hysteresis` ป้องกัน item เด้งเข้า-ออกรอบขอบ budget (ตัวแปรสำคัญต่อ churn)
tie-break ด้วย `item.id` เสมอ (D-08)

---

## 5. `somaos/broker/opt/oracle.py`

```python
@dataclass(frozen=True, slots=True)
class OptResult:
    mode: Literal["exact_belady", "upper_bound"]
    strict_recall: float
    partial_recall: float
    tokens_per_query: float
    per_query: tuple[dict, ...]      # structured, ไม่ใช่ print

def opt_offline(trace: Trace, *, budget_tokens: int, mode: str) -> OptResult: ...
```

---

## 6. `somaos/bench/metrics.py` — schema ของ JSONL หนึ่งบรรทัด

```json
{
  "run_id": "sha256:...",
  "policy": "S",
  "regime": "uniform",
  "seed_root": "holdout-07",
  "trace_id": "sha256:...",
  "budget_tokens": 4096,
  "tau_ticks": 32,
  "n_ticks": 5000,
  "n_queries": 400,
  "strict_recall": 0.0,
  "partial_recall": 0.0,
  "tokens_per_query": 0.0,
  "total_tokens": 0,
  "llm_calls": 0,
  "llm_call_ratio": 0.0,
  "hit_at_k": {"1": 0.0, "5": 0.0, "10": 0.0},
  "context_churn_rate": 0.0,
  "thrash_indicator": 0.0,
  "encode_rate": 0.0,
  "evictions": 0,
  "opt_strict_recall": 0.0,
  "opt_mode": "exact_belady",
  "competitive_ratio": 0.0,
  "surprise_utility_spearman": 0.0,
  "config_hash": "sha256:..."
}
```
Timing (`fast_path_ms_p50/p95`) เขียนแยกไฟล์ `runs/timing-*.jsonl` เพราะไม่ deterministic (DoD §4)
