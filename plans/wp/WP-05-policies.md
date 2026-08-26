# WP-05 — Policy interface + baselines B0–B4

**Depends on:** WP-01  **Blocks:** WP-06, WP-07, WP-08  **Est:** 2 d

## Goal
`somaos/broker/policy.py` (protocol + registry) และ baseline ทั้ง 5 ตัวตาม §7.2
ทุกตัว implement interface เดียวกัน สลับผ่าน config (§15 ข้อ 3)

## B0 — `b0_full.py` (full context)
- เก็บทุก item ที่เคยเห็น, `on_query` คืนทุกอย่าง, `ignores_budget = True`
- **บทบาท:** upper bound คุณภาพ + upper bound cost
- **sanity gate:** ต้องได้ `strict_recall == 1.0` ทุก regime ไม่งั้น ground truth พัง (WP-02 §5.4)

## B1 — `b1_window.py` (sliding window last-K)
- เก็บ item ล่าสุดที่รวม tokens ≤ budget (ไล่จากใหม่ไปเก่า)
- ไม่สนใจ query เลย

## B2 — `b2_rag.py` (naive RAG top-k) ← **คู่แข่งที่ต้องชนะ (kill criterion ข้อ 1)**
- ไม่มี embedding model → ใช้ **lexical/graph similarity บน ground-truth tags**:
  `sim(item, query) = 0.6·jaccard(topics) + 0.4·jaccard(entities)`
- `on_query`: จัดอันดับทั้ง store ด้วย sim → เติมลง bundle จนเต็ม budget
- **หมายเหตุสำคัญ:** นี่คือ RAG ที่ retrieval **แม่นเกินจริง** เพราะใช้ tag จาก world ตรง ๆ
  → เป็น baseline ที่ *แข็งกว่า* RAG จริง ถ้า S ชนะตัวนี้ได้ ผลยิ่งน่าเชื่อ
  ต้องเขียนไว้ใน docstring + report

## B3 — `b3_summarize.py` (summarize every N turns)
- ทุก `summarize_every` ticks: ยุบ item ที่เก่ากว่า `keep_recent` เป็น summary item
- lossy แบบ deterministic ตาม **D-12** (เก็บเฉพาะ top `retain_fraction` by surprise, ที่เหลือหายถาวร)
- `summary.tokens = ceil(Σ tokens * compression_ratio)`
- `summary.source_item_ids` เก็บ id ของ item ที่ยัง "ถูกครอบคลุม" เท่านั้น
  → query ที่ต้องการ item ที่หายไป จะตอบไม่ได้ (นี่คือเจตนา)
- นับ `llm_calls += 1` ต่อการ summarize หนึ่งครั้ง (modeled cost)

## B4 — `b4_paging.py` (MemGPT-style LLM-managed paging) — **cost-model proxy**
- ทุก `paging_interval` ticks หรือเมื่อ working set เต็ม → เรียก `_simulated_llm_page_decision()`
  ซึ่งเป็น heuristic (เช่น recency + sim ต่อ goal ปัจจุบัน) + `llm_calls += 1`
- เพิ่ม `paging_token_surcharge` ต่อการเรียก (จำลอง prompt ที่ให้โมเดลตัดสินใจ page)
- **ต้องมี docstring และบรรทัดใน report ว่า:** นี่ไม่ใช่ MemGPT จริง
  เป็นตัวแทนเชิงต้นทุน/determinism เท่านั้น การเทียบจริงเกิดที่ Phase 0.5 / L2 (master plan §3.4)

## Registry
```python
@register_policy("B2")
class NaiveRagPolicy: ...

build_policy("B2", budget_tokens=4096, seed_root="dev-01", config={...})
```
config ของแต่ละ policy อยู่ใน `bench/configs/policies.json` ห้าม hardcode ตัวเลขในคลาส

## Acceptance (tests/test_policies.py)
1. **conformance suite ที่รันกับทุก policy** (parametrize): reset → observe×N → on_query
   - bundle ไม่เกิน budget (ยกเว้น B0)
   - `bundle.query_id == q.id`, `bundle.tick == q.tick`
   - เรียก `reset()` ซ้ำแล้ว state เดิมหายจริง (รัน trace เดียวกันสองรอบ → ผลเท่ากัน)
   - deterministic: seed เดิม → `bundle_hash` sequence เท่ากัน
2. B0: `strict_recall == 1.0` บน trace ทดสอบ
3. B1: item ใน bundle ต้องเป็น K ตัวล่าสุดจริง
4. B2: item ที่ sim สูงสุดต้องอยู่ใน bundle เสมอถ้า budget พอ
5. B3: หลัง summarize item ที่ไม่ติด `retain_fraction` ต้องหายจาก store จริง
6. B4: `stats()["llm_calls"] > 0` และ token surcharge ถูกนับ

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-05-policies.md` ตาม `plans/02_INTERFACES.md` §2
> เขียน conformance test เป็น parametrized fixture ก่อน แล้วให้ทุก policy ผ่านชุดเดียวกัน
> อ่าน D-12 ก่อนทำ B3 และ master plan §3.4 ก่อนทำ B4 — ทั้งสองมีข้อจำกัดที่ตั้งใจ ห้าม "แก้ให้ดีขึ้น"
