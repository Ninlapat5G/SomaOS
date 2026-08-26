# WP-03 — Retention scoring engine ★

**Depends on:** WP-01  **Blocks:** WP-06  **Est:** 1.5 d
**นี่คือ 1 ใน 3 ชิ้นที่ §7.1 อนุญาต — pure function, coverage ≥ 95% (§15 ข้อ 4)**

## Goal
implement `somaos/broker/retention.py` ตาม `plans/02_INTERFACES.md` §3

## สูตร (§5.3 + D-04)
```
retention = w₁·recency_decay + w₂·access_frequency + w₃·semantic_relevance(goal)
          + w₄·surprise + w₄ᵦ·novelty + w₅·pinned + w₆·recompute_cost
```
หารด้วย Σw → อยู่ใน [0,1]

## Feature definitions (ล็อกไว้ ห้ามเปลี่ยนเงียบ)
```
age            = now_tick - stat.last_access_tick
recency        = exp(-age / tau_ticks)                       # Denning working set
frequency      = log1p(stat.access_count) / log1p(max_access_count)   # ต้าน heavy hitter
relevance      = jaccard(item.topics, goal_topics) * 0.6
               + jaccard(item.entities, goal_entities) * 0.4
surprise       = item.surprise
novelty        = item.novelty
pinned         = 1.0 if item.pinned else 0.0
recompute      = item.recompute_cost
```
- `max_access_count` ส่งเข้ามาจาก allocator (ห้ามให้ retention เก็บ state)
- ถ้า `goal_topics` ว่าง → `relevance = 0.0` (ไม่ใช่ 1.0)
- `tau_ticks` มาจาก config (D-03)

## เขียน test ก่อน (§15 ข้อ 4)
`tests/test_retention.py` ต้องมีอย่างน้อย:
1. bounded: property test — feature สุ่มใน [0,1] × weight สุ่ม ≥ 0 → score ∈ [0,1]
2. monotone ต่อทุก feature ที่ weight > 0
3. zero-weight isolation: ตั้ง `w_surprise=0` → เปลี่ยน surprise ไม่กระทบ score เลย
4. recency decay: `age == tau_ticks` → recency ≈ 1/e (tolerance 1e-9)
5. frequency saturation: access_count 1000 vs 2000 ต่างกันน้อยกว่า 1 vs 2
6. determinism: เรียก 1000 ครั้ง ได้ float bit-identical
7. no side effect: item/stat ที่ส่งเข้าไปไม่ถูกแก้ (ตรวจด้วยการ deepcopy เทียบ)
8. golden test: ชุด input คงที่ 20 เคส → คาดหวังค่าใน `tests/golden/retention.json`

## จุดที่ผิดง่าย
- `exp(-age/tau)` เมื่อ `age` ใหญ่มาก → underflow เป็น 0.0 (ยอมรับได้ แต่ต้องไม่ NaN)
- `age` ติดลบไม่ได้ → ถ้า `last_access_tick > now_tick` ให้ raise ไม่ใช่ clamp เงียบ ๆ
- jaccard ของ empty set กับ empty set = 0.0 (ไม่ใช่ 1.0)
- float summation order ต้องคงที่ → บวกตามลำดับ field ที่ประกาศ ห้าม iterate dict

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-03-retention.md` แบบ **test-first**: เขียน `tests/test_retention.py` ทั้งหมดก่อน
> แล้วค่อยเขียน `somaos/broker/retention.py` ให้ผ่าน
> ห้ามให้ฟังก์ชันใดใน retention.py แตะ I/O, global state, หรือ random
> รายงาน coverage ของไฟล์นี้ตอนจบ
