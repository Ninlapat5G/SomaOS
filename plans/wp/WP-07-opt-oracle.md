# WP-07 — OPT oracle harness ★

**Depends on:** WP-02, WP-05  **Blocks:** WP-08  **Est:** 2 d
**อ่าน D-09 และ master plan §3.1 ก่อนเขียนบรรทัดแรก**

## Goal
`somaos/broker/opt/oracle.py` — ตอบว่า "ถ้ารู้อนาคตทั้งหมด ที่ budget เท่านี้ ตอบ query ได้กี่ %"
นี่คือตัวส่วนของ `competitive_ratio` ซึ่งเป็น **ตัวชี้ขาดของ Phase 0**

## โครงปัญหา
```
ให้ trace ที่จบแล้ว, budget B tokens
ทุก tick มี resident set ⊆ items ที่เกิดมาแล้ว, Σ tokens ≤ B
query q ตอบได้ (strict) ⟺ required_item_ids(q) ⊆ resident(q.tick)
maximize จำนวน query ที่ตอบได้
```

## Mode 1 — `exact_belady` (regime `uniform` เท่านั้น)
เมื่อทุก item มี tokens เท่ากัน → capacity = B / token_size slots
ปัญหาเทียบเท่า offline caching → **Belady MIN optimal**:
- ตอน admit ต้อง evict → evict item ที่ `next_required_tick` ไกลที่สุด (∞ ถ้าไม่ถูกใช้อีก)
- ต้อง precompute `next_required` ต่อ (item_id, tick) ด้วยการเดินย้อนหลังจากท้าย trace

⚠️ ข้อควรระวัง: Belady optimal สำหรับ **cache hit maximization ต่อ single-item request**
ที่นี่ query ต้องการ **หลาย item พร้อมกัน (all-or-nothing)** → Belady ตรง ๆ ไม่ optimal เป๊ะ
**วิธีจัดการ:** ทำ Belady บนระดับ "query satisfaction" ด้วย
`next_required_tick(item) = tick ของ query ถัดไปที่ต้องการ item นี้`
แล้ว **ยืนยันด้วย brute-force optimal บน trace เล็ก** (n_ticks ≤ 60, items ≤ 12, capacity ≤ 4)
ถ้า Belady-variant ไม่เท่า brute force → ปรับชื่อโหมดเป็น `near_optimal` และรายงานช่องว่าง
**ห้ามเรียกว่า exact ถ้า test เทียบ brute force ไม่ผ่าน**

## Mode 2 — `upper_bound` (regime อื่น ๆ, item ขนาดไม่เท่ากัน)
NP-hard → คำนวณ **upper bound ที่ตอบง่ายและ sound**:
```
UB1 (per-query feasibility): query q ตอบได้ก็ต่อเมื่อ Σ tokens(required(q)) ≤ B
     → UB_strict_recall = |{q : Σtokens(required(q)) ≤ B}| / |Q|
```
นี่คือ bound ที่หลวมแต่ **sound แน่นอน** (ไม่มี policy ไหนทำได้เกินนี้)
เพิ่ม UB2 ที่แน่นขึ้นถ้ามีเวลา: LP relaxation / interval-packing บนหน้าต่างเวลา
รายงานเสมอว่าใช้ bound ตัวไหน และ `competitive_ratio` ที่ได้เป็น **lower bound ของค่าจริง**

## API
```python
def opt_offline(trace, *, budget_tokens: int, mode: Literal["exact_belady","upper_bound","brute_force"]) -> OptResult
def next_required_map(trace) -> dict[str, list[int]]
def brute_force_optimal(trace, *, budget_tokens) -> OptResult   # ใช้เฉพาะ trace จิ๋ว, มี guard ขนาด
```
`brute_force_optimal` ต้อง raise `TraceTooLarge` ถ้า state space เกินขีด (กัน hang)

## Acceptance (tests/test_opt.py)
1. `opt_strict_recall >= strict_recall` ของทุก policy บน trace เดียวกัน budget เดียวกัน
   ← **test นี้สำคัญที่สุด** ถ้า policy ไหนชนะ OPT แปลว่า oracle ผิดหรือมี leak
2. budget = ∞ → `opt_strict_recall == 1.0`
3. budget < min(Σ tokens ของ required ของทุก query) → `opt_strict_recall == 0.0`
4. `exact_belady` == `brute_force` บน trace จิ๋ว 30 ชุด (ถ้าไม่ผ่าน → เปลี่ยนชื่อโหมด ดู §Mode 1)
5. `upper_bound >= exact_belady` บน trace uniform เดียวกัน (bound ต้อง sound)
6. deterministic

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-07-opt-oracle.md`
> จุดที่ต้องซื่อสัตย์ที่สุดของโปรเจกต์คือไฟล์นี้ — ถ้า Belady-variant ไม่เท่า brute force
> **ห้ามปรับ brute force ให้เข้าหา Belady** ให้เปลี่ยนชื่อโหมดและรายงานช่องว่างแทน (§15 ข้อ 7)
> ถ้า acceptance ข้อ 1 fail → หยุดทุกอย่างแล้วรายงาน เพราะแปลว่ามี leak ใน harness
