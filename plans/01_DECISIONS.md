# Phase 0 — Locked Decisions

> ข้อตัดสินใจเหล่านี้ **ล็อกแล้ว** Sonnet ห้ามเปลี่ยนโดยไม่ถาม
> ถ้าเจอว่าข้อไหนทำไม่ได้จริง → หยุด รายงาน แล้วรอคำตอบ

---

## D-01 — ขอบเขต: Phase 0 ไม่มี LLM เลย
ทั้ง trace, policy, oracle, metric เป็น deterministic ล้วน
เหตุผล: §7.1 Step 1 — เร็ว ฟรี ทดสอบซ้ำได้ และเป็นขั้นเดียวที่คำนวณ OPT ได้

## D-02 — นิยาม quality ที่ L1
ไม่มี LLM → ไม่มี generation quality ใช้ **answerability** แทน:

```
strict_recall(q)  = 1 ถ้า required_item_ids(q) ⊆ bundle_item_ids  มิฉะนั้น 0
partial_recall(q) = |required ∩ bundle| / |required|
recall_accuracy   = mean(strict_recall)     ← ตัวหลัก
```
รายงาน `partial_recall` ควบคู่เสมอ (strict อย่างเดียวหยาบเกินไปตอน budget ต่ำ)

**เหตุผลที่ strict เป็นตัวหลัก:** ถ้าขาด evidence ชิ้นเดียว คำตอบก็ผิดอยู่ดี — สะท้อนโลกจริงมากกว่า

## D-03 — คำตอบของ §13 ข้อ 2 (นิยาม τ ของ working set)
Phase 0 ใช้ **τ คงที่ต่อ config** (`tau_ticks`) ไม่ทำ adaptive
เหตุผล: adaptive τ เพิ่มตัวแปรอิสระอีกตัว ทำให้แยกไม่ออกว่าผลมาจาก retention score หรือมาจาก τ
sweep τ ∈ {8, 32, 128} เป็น config axis แทน แล้วรายงาน sensitivity
→ adaptive τ เป็นคำถามของ Phase 1 ไม่ใช่ Phase 0

## D-04 — คำตอบของ §13 ข้อ 3 (surprise ของ observation ที่ไม่มี belief ทำนาย)
แยกเป็น **หมวดของตัวเอง** ไม่ใช่ surprise สูงสุด

```
novelty  = 1.0   ถ้าไม่มี predictor ครอบคลุม obs นี้เลย
surprise = 1 - confidence(predictor)   ถ้ามี predictor
```
`retention` ใช้ทั้งสองเป็น feature แยกกัน (`w4·surprise + w4b·novelty`)

**เหตุผล:** ยุบรวมกันจะทำให้ "เรื่องใหม่ที่ไม่สำคัญ" (noise) ได้คะแนนเท่ากับ
"เรื่องที่ขัดความเชื่อเดิมแรง ๆ" ซึ่งเป็นคนละสัญญาณ และเป็นจุดที่ kill criterion ข้อ 4 จะจับได้

## D-05 — §13 ข้อ 1, 4, 5, 6 → deferred
belief revision semantics / consolidation cadence / shared episodic / schema migration
ทั้งหมดไม่จำเป็นต่อการตอบคำถามของ Phase 0 → **ห้ามแตะ** บันทึกไว้ที่ `plans/05_PHASE1_PLUS_OUTLINE.md`

## D-06 — Cost model ที่ L1
```
tokens_per_query = tokens ของ ContextBundle ที่ส่งคืนตอน query
total_tokens     = Σ tokens_per_query  (+ paging surcharge ของ B4)
llm_call_ratio   = llm_calls / decisions     (L1: 0 ทุก policy ยกเว้น B3/B4 ที่ใช้ค่า modeled)
```
**cost หลักคือ tokens ของ bundle ตอน query** ไม่ใช่ขนาด store — เพราะสิ่งที่จ่ายเงินจริงคือสิ่งที่ส่งเข้าโมเดล

## D-07 — Reference cost model สำหรับ kill criterion §7.4 ข้อ 3
L1 ไม่มี LLM ให้เทียบ → ล็อกค่าอ้างอิงไว้ตายตัว:

```
REF_LLM_CALL_MS = 800.0     # 1 model call ~ 800ms wall clock
REF_TICK_LLM_CALLS = 0.1    # สมมติ 10% ของ tick แตะ LLM (จากเป้า §8.2)
budget_ms_per_tick = REF_LLM_CALL_MS * REF_TICK_LLM_CALLS * 0.05   # = 4.0 ms
```
เกณฑ์: `p95(fast_path_ms_per_tick) ≤ 4.0 ms` ที่ N_items = 10,000
ค่าคงที่พวกนี้อยู่ใน `bench/configs/phase0.json` ไม่ hardcode ในโค้ด

## D-08 — Determinism contract
- ทุก randomness ผ่าน `random.Random(seed)` ที่ derive จาก `seed_root` แบบ named stream:
  `stream_seed = int.from_bytes(sha256(f"{seed_root}:{stream_name}").digest()[:8], "big")`
- ห้ามใช้ `random` module-level, ห้าม `np.random` global, ห้าม `set`/`dict` ordering ในการตัดสินใจ
  (ถ้าต้อง iterate ให้ sort ด้วย key ที่ชัดเจน; tie-break ด้วย `item.id` เสมอ)
- ห้ามใช้ `hash()` ของ Python (มี PYTHONHASHSEED) → ใช้ `hashlib.sha256`

## D-09 — Item ขนาดไม่เท่ากัน (สำคัญต่อ OPT)
`MemoryItem.tokens` แปรผันได้ → OPT ที่แท้จริงเป็น NP-hard
Phase 0 รันสองโหมดเสมอ:
- `uniform` regime: ทุก item tokens เท่ากัน → **OPT-exact ด้วย Belady MIN** → `competitive_ratio` ที่เชื่อถือได้
- `variable` regime: tokens แปรผัน → **OPT-UB (upper bound)** → `competitive_ratio` เป็นค่าต่ำกว่าจริง

**ตัวเลขที่ใช้ตัดสิน kill criterion ข้อ 2 คือของ `uniform` regime** (เพราะเป็นตัวเดียวที่เป็น ratio จริง)
`variable` regime รายงานเป็น supporting evidence

## D-10 — Weight vector ต้องมาจากไฟล์ ไม่ใช่โค้ด
`RetentionWeights` โหลดจาก config เท่านั้น มี `default_weights.json` หนึ่งชุดต่อ pack profile
**ห้าม tune weight หลังเห็นผล test set** — ถ้าจะ tune ให้ทำบน `seed_root` ชุด `dev` เท่านั้น
แล้วรายงานผลบน `holdout` seeds ที่ไม่เคยเห็น (WP-08 §4)

## D-11 — Tier ของ memory (คนละเรื่องกับ agent scheduling tier)
```
Tier.WORKING = 0   # อยู่ใน context budget ตอนนี้
Tier.WARM    = 1   # ดึงกลับได้ถูก (in-process index)
Tier.COLD    = 2   # ต้อง scan/rebuild
```
FOCUS/AMBIENT/DORMANT ใน §8.2 เป็น **agent scheduling tier** → เป็นของ Phase 3 ห้ามปนกัน

## D-12 — B3 (summarize) ต้อง lossy แบบ deterministic
ไม่มี LLM → summarization = ยุบ k items เป็น 1 summary item ที่
`tokens = ceil(Σtokens * compression_ratio)` และ **เก็บได้เฉพาะ `retain_fraction` items ที่ surprise สูงสุด**
ที่เหลือถือว่าสูญหายถาวร (ไม่มี pointer กลับ raw)

**เจตนา:** จำลอง baseline ที่ละเมิด §4.3 กฎข้อ 3 — คือจุดที่ SomaOS อ้างว่าดีกว่า
ต้องเขียน docstring บอกไว้ว่านี่คือการจำลองข้อเสียโดยตั้งใจ ไม่ใช่ bug

## D-13 — strict_recall ต้องนับ coverage ผ่าน source_item_ids ด้วย (ส่วนขยายของ D-02)
เมื่อ B3 ยุบ item เป็น summary (D-12) item เดิมจะไม่มี id ของตัวเองในระบบอีกต่อไป
ถ้า `bundle_item_ids` นับเฉพาะ `{it.id for it in bundle.items}` เฉย ๆ B3 จะตอบ query ที่อ้างถึง
item ที่ถูก "เก็บรักษาไว้" ใน summary (คือ id ที่อยู่ใน `retain_fraction`) ไม่ได้เลย — ทั้งที่ระบบ
ตั้งใจให้ pointer นั้นยังใช้งานได้ (§4.3 กฎ 3 บางส่วน)

จึงนิยาม (ใน `bench/metrics.py`):
```
bundle_item_ids = {it.id for it in bundle.items} | {sid for it in bundle.items for sid in it.source_item_ids}
strict_recall(q) = 1 ถ้า required_item_ids(q) ⊆ bundle_item_ids  มิฉะนั้น 0
```
item ที่ไม่ติด `retain_fraction` (ไม่มีใน `source_item_ids` ของ summary ไหนเลย) ยังคงหายถาวรตามเจตนาของ D-12
**หมายเหตุ:** เป็น single-level lookup (ไม่ recursive) — Phase 0 ไม่มี summary-of-summary
