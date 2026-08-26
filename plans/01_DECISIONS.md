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

## D-14 — pointer dereference ต้องจ่าย token (page fault) — **แก้ไข D-13**

> สถานะ: อนุมัติแล้ว 2026-08-26 — แทนที่กฎการนับ coverage ของ D-13
> D-13 ยังคงอยู่ในไฟล์นี้เพื่อเป็นบันทึกว่าเคยตัดสินใจอะไรไว้ และทำไมถึงต้องแก้

### ปัญหาที่พบ

D-13 ให้ `bundle_item_ids` รวม `source_item_ids` ด้วย **โดยไม่คิดค่าใช้จ่ายใด ๆ**
การวินิจฉัยบน `uniform / dev-01` พบว่า:

```
ตอบถูกเพราะ item อยู่ใน context จริง :  12/163  ( 7.4%)
ตอบถูกเพราะ pointer เฉย ๆ            : 151/163  (92.6%)
item ขนาด 100 token แบก source_item_ids ได้ 108 ids  (≈0.93 token ต่อ id)
```

กระดานคะแนนจึงแยกไม่ออกระหว่าง "จำได้" กับ "ทิ้งของแล้วเก็บใบเสร็จไว้"
policy ที่ทิ้งทุกอย่างแล้วเก็บแต่ id จะได้ `strict_recall = 1.000` ฟรี ๆ

### กฎใหม่

ยึดตาม `target_SomaOS.md` §4.1 ที่ map `Page fault → retrieval miss` อยู่แล้ว
pointer = page ที่ไม่ resident → การใช้งานต้อง **fault กลับเข้ามา** และเสีย token เท่าขนาด raw item

```
resident  = {it.id for it in bundle.items}          # จ่ายไปแล้วตอนใส่ bundle → ฟรี
residual  = budget_tokens − Σ(it.tokens for it in bundle.items)

fault_queue = [sid  for it in bundle.items          # เรียงตามลำดับ item ใน bundle
                    for sid in it.source_item_ids   # แล้วตามลำดับที่ policy เขียนไว้เอง
               if sid not in resident]              # (dedupe, คงลำดับแรกที่เจอ)

เดินคิวจากหัว จ่าย raw_tokens[sid] ไปเรื่อย ๆ จนกว่า residual จะไม่พอ
เจอตัวแรกที่จ่ายไม่ไหว → หยุด ที่เหลือทั้งคิวเป็น deferred

covered = resident ∪ faulted
```

`raw_tokens` มาจาก **trace** ไม่ใช่จาก state ของ policy — policy จึงกำหนดราคาของตัวเองไม่ได้

### ⚠️ resolver ต้อง "มองไม่เห็นเฉลย" — จุดที่พลาดในร่างแรก

ร่างแรกของ D-14 ให้ resolver รับ `required_item_ids` เข้าไปด้วย แล้ว fault เฉพาะ id
ที่ query ต้องการ โดยเรียงจากถูกไปแพงเพื่อ "ให้ประโยชน์แก่ policy มากที่สุด"

**ร่างนั้นไม่ปิดช่องโหว่เลย** — policy ที่เก็บแต่ใบเสร็จยังได้ `strict_recall = 1.000` เหมือนเดิม
เพราะการ fault ถูกชี้เป้าด้วยเฉลย = แจก **prefetcher ที่รู้อนาคต** ให้ทุก policy ฟรี ๆ
(ยืนยันด้วย `test_pointer_hoarder_cannot_win_end_to_end` ซึ่ง fail ตอนนั้น)

ของจริงจึงต้องเป็น: **resolver ไม่รับ `required_item_ids` เลย** รับแค่ bundle
ลำดับการ fault มาจาก**ลำดับที่ policy เขียน pointer ไว้เอง** ซึ่งคือการประกาศ priority ของมันเอง
policy ไหนอยากได้ page ไหนกลับมา ต้องจัดลำดับให้ถูก — เหมือนระบบจริงที่ไม่มีใครรู้เฉลยล่วงหน้า

หลักการเดียวกับที่ `QueryView` ไม่มีฟิลด์ `required_item_ids` ตั้งแต่แรก (D-02/WP-06)
คือทำให้การรั่วของเฉลย **เป็นไปไม่ได้เชิงโครงสร้าง** ไม่ใช่แค่ "ระวังอย่าใช้"
มี test คุมไว้ที่ `test_resolver_signature_cannot_see_the_answer_key`

### ผลกระทบที่ตั้งใจให้เกิด

- `B3` (summarize) ยังตอบผ่าน summary ได้ตามเจตนาเดิมของ D-13 — แต่ต้อง**จ่ายค่าคลายบีบอัด**
  ซึ่งตรงกับความเป็นจริง และตรงกับ §4.3 กฎ 3 (raw ไม่ถูกทำลาย เก็บ pointer กลับได้)
- `S` (counter-merge) เสียประโยชน์จากช่องโหว่นี้ทั้งหมด → ตัวเลขจะ**ตกลง** ซึ่งถูกต้องแล้ว
  และเพราะ `source_item_ids` ของ `S` เรียงตามลำดับเวลาที่ merge เข้ามา (ไม่ได้เรียงตามความสำคัญ)
  `S` จึงแทบไม่ได้อะไรจาก fault queue เลย — ซื่อสัตย์ดี เพราะ `S` ไม่เคยประกาศ priority ของ pointer ไว้
- item ที่ไม่ติด `retain_fraction` ยังหายถาวรตาม D-12 เหมือนเดิม
- ค่า token ที่จ่ายไปกับ page fault ถูกบวกเข้า `effective_tokens_per_query` ตาม D-06
  (สิ่งที่ถูกส่งเข้าโมเดลจริงคือ cost จริง)

### metric ใหม่ใน JSONL

`page_faults`, `page_fault_rate`, `page_fault_tokens`, `page_fault_tokens_per_query`,
`answered_via_pointer_rate`, `pointer_denied_rate`, `effective_tokens_per_query`

`answered_via_pointer_rate` คือตัวเลขที่ทำให้ต้องแก้ D-13 — เก็บไว้เป็น regression guard ถาวร
