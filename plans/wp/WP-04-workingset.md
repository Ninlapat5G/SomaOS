# WP-04 — Working set allocator ★

**Depends on:** WP-01 (ใช้ WP-03 ตอนประกอบใน WP-06)  **Blocks:** WP-06  **Est:** 1.5 d

## Goal
implement `somaos/broker/workingset.py` ตาม `plans/02_INTERFACES.md` §4

## Algorithm
1. บังคับ `pinned` เข้าก่อน (ถ้ารวมกันเกิน budget → raise `OverPinned`)
2. คำนวณ `retention_score` ของ candidate ทุกตัว
3. greedy knapsack ด้วย `score / tokens` (density) descending, tie-break `item.id` ascending
4. **hysteresis**: item ที่อยู่ใน WORKING แล้ว จะถูก evict ก็ต่อเมื่อ
   `score_incumbent + hysteresis < score_challenger` — ป้องกัน churn รอบขอบ budget
5. อัปเดต tier: เข้า WORKING / ตกไป WARM (COLD ใช้เมื่อ item ไม่ถูกแตะเกิน `cold_after_ticks`)
6. บันทึก eviction ทุกครั้งเป็น structured record (§5.6 "eviction ที่มีคะแนน + log ว่าลืมอะไรเพราะอะไร")

```python
@dataclass(frozen=True, slots=True)
class EvictionRecord:
    tick: int
    item_id: str
    score: float
    displaced_by: str | None
    reason: Literal["budget", "cold", "superseded"]
```

## Churn & thrashing (§5.5)
```
churn(tick)         = |admitted| + |evicted|
context_churn_rate  = mean(churn) over sliding window (default 32 ticks)
thrash_indicator    = churn_rate_normalized * (1 - progress_rate)
```
`progress_rate` ส่งเข้ามาจากภายนอก (ที่ L1 = fraction ของ query ที่ตอบได้ในหน้าต่างเดียวกัน)

## Acceptance (tests/test_workingset.py)
1. ไม่เคยเกิน budget: property test ด้วย candidate สุ่ม 10k ชุด
2. pinned อยู่ใน resident เสมอเมื่อ budget พอ
3. determinism: candidate ชุดเดิม (สลับลำดับ input) → `resident` เท่ากันทุกครั้ง (ทดสอบด้วย shuffle)
4. hysteresis: ตั้ง `hysteresis=0.1` → churn ต่ำกว่า `hysteresis=0.0` บน trace เดียวกัน อย่างน้อย 20%
5. eviction log: จำนวน record = จำนวน evicted สะสม และทุก record มี reason
6. `OverPinned` ถูก raise ไม่ใช่ silently drop
7. performance: `allocate()` ที่ candidates = 10,000 → p95 ≤ 4.0 ms (D-07) บนเครื่อง dev
   (test นี้ mark `@pytest.mark.perf` แยกออกจาก suite หลัก)

## จุดที่ผิดง่าย
- greedy density ไม่ optimal — **ไม่เป็นไร** เพราะเราเทียบกับ OPT อยู่แล้ว แต่ต้องเขียน docstring บอก
- `tokens = 0` ต้อง guard division by zero
- อย่าเก็บ `MemoryItem` ซ้ำใน dict หลายที่ → ให้ allocator ถือ `dict[str, ItemStat]` อย่างเดียว

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-04-workingset.md` ตาม `plans/02_INTERFACES.md` §4
> allocator ต้อง deterministic แม้ลำดับ input เปลี่ยน — test ข้อ 3 คือหัวใจ
> เก็บ eviction log เป็น structured record ไม่ใช่ logging string
