# 02 — Interfaces (normative)

> **สัญญาที่โค้ดต้องเคารพ** — เขียนจากของที่สร้างจริงแล้ว ไม่ใช่ร่างล่วงหน้า
> v2 · 2026-08-28 · สอดคล้องกับ `plans/03_MEMORY_ARCHITECTURE.md` และ `plans/01_DECISIONS.md`
> ✅ = implement แล้วมี test คุม · 🔨 = ยังไม่ได้สร้าง

---

## 0. แผนผังโมดูล

```
somaos/broker/
├── memory/          ✅ ชั้นล่างสุด — ไม่ import อะไรจากชั้นบนเลย
│   ├── vector.py    ✅ embed · grade D0–D2 · similarity · fidelity · nbytes
│   ├── address.py   ✅ content_address (Merkle) · AliasTable
│   ├── node.py      ✅ Region · MemoryNode · NodeStat · ระดับของ CORE/ARCHIVE
│   └── tree.py      ✅ MemoryTree — โครงสร้าง + แกนความลึก + การเดินแบบมีเพดาน
├── dilution/        ✅ แกนความคมชัด — import memory/ อย่างเดียว
│   └── engine.py    ✅ DilutionEngine · DilutionEvent · compose_fidelity
├── regions/         ✅ กฎเฉพาะของแต่ละภูมิภาค
│   ├── core.py      ✅ CoreSet · CoreZone — ตัวตนที่ resident + quota
│   └── trigger.py   ✅ TriggerRegistry · Trigger FSM — interrupt table
├── recall/          ✅ การนึก — import memory/ + regions/
│   └── machine.py   ✅ RecallMachine · Move · WalkPath · RecallResult
├── policies/        🔨 B0/B1/B2/B2c/B4/S ใต้ contract เดียวกัน
└── opt/             ✅(เดิม) oracle — ต้องเปลี่ยนเป้าเป็น "จัดสรรคลัง"
```

**กฎการ import (มี test คุมที่ `tests/test_layering.py`):** ชั้นล่างห้ามรู้จักชั้นบน
`memory/` ไม่ import `dilution/`, `regions/`, `recall/` เด็ดขาด
dependency ภายนอก: **stdlib + numpy เท่านั้น** (ตรวจด้วย `sys.stdlib_module_names`)

---

## 1. `memory/vector.py` ✅

```python
DEFAULT_DIM = 256

class Grade(IntEnum):        # เรียงตาม "อะไรถูกทำลาย" ไม่ใช่ "ประหยัดกี่ไบต์"
    D0_EXACT   = 0           # float32  — ตอบได้ทั้งชิ้นไหนและแนวไหน
    D1_INT8    = 1           # int8     — ยังตอบได้ทั้งสอง (recall@10 ≈ 0.98–0.996)
    D2_BINARY  = 2           # sign bit — ตอบได้แต่ "แนวไหน" (0.79–1.00)
    D3_MERGED  = 3           # ไม่มีเวกเตอร์ของตัวเอง — tree เป็นคนจัดการ
    D4_COUNTER = 4           # เหลือแค่ตัวนับที่บรรพบุรุษ

embed(keys, *, dim, seed_root) -> ndarray      # deterministic (N-13)
cue_vector(topics, entities, *, dim) -> ndarray
encode(vec, grade) -> ndarray                  # ขึ้น D3/D4 → GradeError
similarity(a, b) -> float
fidelity_of(original, current) -> float        # clamp ที่ 0
nbytes(vec, grade) -> int                      # binary คิด 1 bit/มิติ ไม่ใช่ 1 byte
vector_digest(vec, grade) -> str
```

**ห้ามลดมิติก่อน binary** — sign bit คือ SimHash, จำนวนบิต = ขนาดตัวอย่างของการประมาณมุม
ลดมิติ = ลดความแม่นเร็วกว่าที่ประหยัดได้ (วัดแล้ว ดู `03` §3.3)

## 2. `memory/address.py` ✅

```python
content_address(*, vec, grade, level, region, children) -> "addr:<sha256>"
    # children ถูก sort → ลำดับพี่น้องไม่ทำให้ address แตก
    # grade อยู่ใน address → ของที่เจือจางแล้วคือคนละ address

class AliasTable:            # append-only ห้ามลบ
    add(old, new)            # ชี้ซ้ำไปที่อื่น → ValueError (ห้ามเขียนประวัติใหม่)
    resolve(addr) -> str     # ไม่มี alias → คืนตัวเอง · ห้ามคืน None
    chain(addr) -> tuple     # เส้นทางเต็ม = audit trail ของการจางของความทรงจำหนึ่ง
    links -> dict            # สำเนา ป้องกันการแก้ประวัติจากภายนอก
```

## 3. `memory/node.py` ✅

```python
class Region(IntEnum):  CORE=0 · TRIGGER=1 · SKILL=2 · ARCHIVE=3
UNDILUTABLE = {CORE, TRIGGER}                    # N-06
MAX_GRADE   = {CORE: D0, TRIGGER: D0, SKILL: D3, ARCHIVE: D4}

class CoreLevel(IntEnum):     TRAIT=0 · ADAPTATION=1 · NARRATIVE=2   # ช้า→เร็ว (McAdams)
class ArchiveLevel(IntEnum):  VERBATIM=0 · SPECIFIC_EVENT=1 ·
                              GENERAL_EVENT=2 ★ · LIFETIME_PERIOD=3 · NARRATIVE=4
WALK_ENTRY_LEVEL = GENERAL_EVENT                 # ★ จุดเข้าของการเดิน (Conway)

@dataclass(frozen=True) MemoryNode:
    addr · region · level · vec · grade · fidelity
    parent · children · n_merged · span · keys · text_ref · raw_refs
    .nbytes            # คิดเฉพาะเวกเตอร์ — metadata ไม่กินงบ "ขนาดสมอง"
    .may_dilute_to(grade) -> bool

@dataclass NodeStat:   last_used_tick · use_count · hit_count · miss_count
make_node(...) -> MemoryNode                     # คำนวณ addr + fidelity ให้
```

**node เป็น frozen** — เนื้อหากำหนด address การแก้ในที่จะทำให้ address ที่ชี้มาพังเงียบ ๆ
การเจือจางจึง **สร้าง node ใหม่ + เขียน alias** ไม่ใช่แก้ของเดิม
**`NodeStat` แยกออกมา** เพราะการอ่านความทรงจำต้องไม่เขียนทับมัน

## 4. `memory/tree.py` ✅

```python
class MemoryTree:
    insert(node, *, parent=None, tick=0) -> addr      # เนื้อหาซ้ำ = addr เดิม (dedupe)
    resolve(addr) -> Resolution                       # ห้ามคืน None (I1)
    get(addr) -> MemoryNode | None                    # ดิบ ไม่ตาม alias
    by_key(key) -> tuple[addr]                        # exact lookup — ที่ SKILL/TRIGGER ใช้
    entry_points(region) -> tuple[addr]               # ชั้น general event
    rank_children(addr, cue, *, tick, beam) -> ((addr, score), ...)
    touch(addr, *, tick, hit)                         # ยกความลึกเท่านั้น ไม่แตะ fidelity
    retrieval_strength(addr, *, tick) -> float
    replace_node(old, new) -> addr                    # fidelity สูงขึ้น → ValueError
    dissolve_into_parent(addr, *, counted) -> addr    # D3 ยุบเข้า centroid / D4 นับ
    store_bytes() · region_bytes(r) · grade_histogram() · mean_fidelity()

@dataclass(frozen=True) Resolution:
    node · fidelity · hops · requested
```

⚠️ **`Resolution.fidelity` เป็น *ขอบล่าง* ไม่ใช่ค่าที่วัดได้** — เก็บ cosine ต่อ hop แล้ว compose
ตอนอ่าน (มุมบวกกัน ตาม triangle inequality) จึง**ต่ำกว่าความจริงเสมอ ไม่มีทางสูงกว่า**
แต่หลวมมากหลังหลายขั้น (อ่าน 0.0 ได้ทั้งที่ยังอยู่ใน cluster ถูก)
→ **ใช้ตัดสิน D3/D4 และลง audit log ได้ · ห้ามรายงานเป็น M1** — M1 วัดกับ ground truth ใน bench
(บทเรียนเดิม: ห้ามให้ component ตั้งราคาผลงานตัวเอง)

## 5. `dilution/engine.py` ✅

```python
COUNTER_FLOOR = 0.7          # ต่ำกว่านี้ → D4 (นับอย่างเดียว) ไม่ให้ไปดึง gist ของแม่เพี้ยน

class DilutionEngine:
    store_budget_bytes: int
    enforce(tree, *, tick) -> tuple[DilutionEvent, ...]   # idempotent
    reserved_bytes(tree) · available_bytes(tree)

@dataclass(frozen=True) DilutionEvent:      # ลง JSONL ได้ — audit trail
    tick · addr_before/after · region · grade_before/after
    fidelity_before/after · bytes_before/after · retrieval_strength · reason
```

**ลำดับ: rung-major ไม่ใช่ victim-major** — ทุกตัวขึ้น int8 ก่อน แล้วค่อยทุกตัวขึ้น binary
เพราะ int8 แทบไม่เสียความสามารถแต่คืน 4 เท่า → เก็บของถูกให้หมดก่อนจ่ายของแพง
ภายในขั้นเดียวกัน **เย็นสุดไปก่อน** ซึ่งเป็นจุดที่ "ของที่ไม่ได้ใช้คือของที่จาง" เข้ามาจริง

## 6. `regions/` ✅

```python
class CoreSet:                                    # ตัวตน — resident เสมอ
    quota_bytes: int
    admit(tree, node, level) -> addr              # เกิน quota → CoreQuotaExceeded
    zones(tree) -> (CoreZone, ...)                # เรียง TRAIT → ADAPTATION → NARRATIVE
    resident_tokens(tree) -> int                  # จ่ายทุก tick ก่อนการนึกใด ๆ

class TriggerKind(Enum):   EVENT · TIME · PREDICATE
class TriggerState(IntEnum): ARMED · FIRED · SUSPENDED · RETIRED

class TriggerRegistry:
    arm(trigger) -> id
    on_event(cue, *, tick)  -> fired      # O(1) · 0 ops        (spontaneous retrieval)
    on_tick(tick)           -> fired      # heap · 0 ops
    check_predicates(world, *, tick, evaluate=None) -> fired
                                          # 1 op ต่อเงื่อนไขที่ armed  (monitoring)
    complete(id, *, tick) / suspend(id) / retire(id)
    monitoring_load() -> int              # ภาษีต่อ tick ที่ agent เลือกจ่ายเอง
```

**เรียง zone ของ CORE จากช้าไปเร็ว** = ลำดับใน prompt ด้วย → prefix เสถียร cache ไม่แตก
**`SUSPENDED` ≠ `RETIRED`** — ความตั้งใจที่ค้างยังผุดเอง ที่ทำเสร็จแล้วไม่ผุด

## 7. `recall/machine.py` ✅

```python
class RecallState(Enum): IDLE · CUE · RESIDENT · NAVIGATE · MATERIALIZE · SETTLE
class Move(Enum):        DESCEND · ASCEND · LATERAL · MATERIALIZE · STOP

class RecallMachine:
    __init__(tree, *, ops_budget, context_budget_tokens, beam, tokens_of)
    begin(*, topics, entities, tick, resident, region) -> RecallState
    offer() -> tuple[Move, ...]                  # เมนู tool ของ agent — เฉพาะที่ถูกกฎ
    step(move, *, addr=None) -> RecallState      # ผิดกฎ → IllegalMove
    run_fast_path(*, max_materialized) -> RecallResult    # ไม่แตะ LLM
    finish() -> RecallResult

@dataclass RecallResult:  nodes · path · tokens_used · resident_tokens
@dataclass WalkPath:      steps · ops_used · stopped_by · materialized · to_jsonable()
```

**`tokens_of` ต้องไม่ขึ้นกับ `text_ref`** ไม่งั้น invariant V1 พังทางอ้อม
default = `structural_tokens` (จาก level) · **bench override ด้วยค่าจริงจาก trace**

---

## 8. Invariant ที่มี test คุมแล้ว

| # | Invariant | test |
|---|---|---|
| I1 | `resolve()` ไม่เคยคืน None | `test_memory_core.py`, `test_memory_tree.py`, `test_dilution.py` |
| I2 | `store_used ≤ store_budget` | `test_dilution.py` |
| I3 | ลบ `text_ref` → เดินเหมือนเดิม bit-for-bit | `test_recall.py` |
| I4 | `CORE`/`TRIGGER` ไม่เคยเจือจาง | `test_dilution.py`, `test_memory_tree.py` |
| I5 | เจือจางทำซ้ำได้ | `test_dilution.py` |
| I6 | ไม่มี O(N) ต่อการนึกหนึ่งครั้ง | `test_memory_tree.py`, `test_recall.py` |
| I7 | `fidelity` ลด/`grade` เดินหน้าอย่างเดียว | `test_dilution.py`, `test_memory_tree.py` |
| I8 | ทุกคำตอบมี `WalkPath` | `test_recall.py` |
| I9 | `SKILL` ไม่ index ด้วย similarity อย่างเดียว | `test_memory_tree.py` (`by_key`) |

## 9. ยังไม่ได้สร้าง 🔨

| ชิ้น | หมายเหตุ |
|---|---|
| `policies/` ใหม่ | `S` ห่อ tree+dilution+recall · `B2c` = flat RAG + บีบอัดสุ่ม (N-14) |
| consolidation FSM | `REPLAY → ABSTRACT → REBALANCE → ENFORCE` (`03` §5.4) — รวมถึงการตกผลึกนิสัย |
| trace generator ใหม่ | query 4 ระดับ (N-12) |
| metric detail/gist | N-11 — วัดกับ ground truth |
| `MemoryPolicy` protocol ใหม่ | ต้องรับ `store_budget_bytes` + `recall_ops_budget` (N-05) |
