# WP-01 — Data contracts (Phase 0 subset)

**Depends on:** WP-00  **Blocks:** 02–07  **Est:** 0.5 d

## Goal
implement `somaos/broker/types.py` ตาม `plans/02_INTERFACES.md` §1 ให้ครบและมี invariant enforcement

## Scope
- dataclass ทั้งหมดใน §1 (frozen + slots ตามที่ระบุ)
- `ContextBundle.tokens` และ `.bundle_hash`
- `ContextBundle.validate(ignores_budget: bool = False)` → raise `BudgetExceeded` ถ้าเกิน
- ยังไม่ต้องมี logic ของ policy/retention

## จุดที่ผิดง่าย
- `frozenset` ใน frozen dataclass ต้อง hashable → `Query` ต้อง hash ได้ ห้ามใช้ `set`
- `bundle_hash` ต้องคิดจาก **ลำดับจริงของ items** (ลำดับมีความหมายเพราะ prefix cache §6)
- ห้ามใช้ `hash()` ของ Python (D-08)
- `tuple` ทุกที่ที่ interface บอกว่า tuple — ห้ามเผลอเป็น list

## Acceptance (tests/test_types.py)
- `bundle_hash` เท่ากันเมื่อ input เท่ากัน, ต่างกันเมื่อสลับลำดับ items
- `bundle_hash` เท่ากันข้าม process ที่ `PYTHONHASHSEED` ต่างกัน
- `validate()` raise เมื่อ `tokens > budget_tokens` และไม่ raise เมื่อ `ignores_budget=True`
- `MemoryItem` เป็น hashable และใช้เป็น dict key ได้
- round-trip: `canonical_json` ของ item → parse กลับ → ค่าเท่าเดิม

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-01-types.md` โดยยึด `plans/02_INTERFACES.md` §1 เป็น spec ตัวจริง
> ห้ามเพิ่ม field ที่ไม่ได้อยู่ใน spec ถ้าคิดว่าจำเป็นให้หยุดแล้วถามก่อน
