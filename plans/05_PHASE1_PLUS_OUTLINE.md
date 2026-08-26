# Phase 1+ — System Outline

> 🚫 **DO NOT BUILD YET** — ทุกอย่างในไฟล์นี้ถูกล็อกไว้หลัง gate ของ Phase 0 (§12, §15 ข้อ 2)
> ไฟล์นี้มีไว้เพื่อให้ boundary contract ของ Phase 0 ไม่ทาสีตัวเองจนมุม เท่านั้น
> Sonnet: ถ้าถูกสั่งให้สร้างอะไรในนี้ ให้ทักท้วงและอ้างไฟล์นี้ก่อน

---

## Entry gate ของแต่ละระบบ

| ระบบ | Phase | ห้ามเริ่มจนกว่า |
|---|---|---|
| Phase 0.5 — LoCoMo / LongMemEval adapter | 0.5 | Phase 0 gate = PASS |
| `kernel/` — L0 | 1 | Phase 0.5 มีตัวเลขเทียบ MemGPT/Mem0/Zep แล้ว |
| `registry/` — L1 | 1 | พร้อม kernel |
| `cortex/` — L2 | 2 | `GATE replay_determinism` ผ่าน |
| `packs/social` — L6 | 3 | `GATE belief_causality` + `memory_causality` ผ่าน |
| `modelbus/` — L4 | 3 | พร้อม pack แรก (ต้องมี LLM ใน loop) |
| Mastodon harness (L4 ladder) | 4 | pack social รัน 10 agents ได้คุณภาพ ≥ B3 ที่ต้นทุน ≤ 40% |
| `trace/` — L5 | 4–5 | มี decision จริงให้ trace |
| `packs/hr` — pack ที่สอง | 6 | kernel ไม่ต้องแก้เพื่อรองรับ (ข้อพิสูจน์ generality) |
| `gates/` — conformance suite | ทยอยตาม 1–5 | แต่ละ gate เขียนพร้อมระบบที่มันตรวจ |

---

## ระบบละ 5 บรรทัด (purpose / boundary / gate)

### Phase 0.5 — Standard benchmark adapter (L2 ladder)
- **Purpose:** เอา policy ชุดเดิมไปรัน LoCoMo / LongMemEval เพื่อเทียบคู่แข่งด้วยตัวเลขมาตรฐาน (§8.6.2)
- **Boundary:** adapter แปลง dataset → `Trace` ของ WP-02 → **policy code ไม่ต้องแก้เลย**
  ← นี่คือเหตุผลที่ `MemoryPolicy` protocol ต้องนิ่งตั้งแต่ Phase 0
- **Gate:** มีตัวเลขเทียบตรงกับ MemGPT/Letta, Mem0, Zep ที่ budget เท่ากัน
- ⚠️ ต้อง re-survey benchmark ล่าสุดก่อนเริ่ม (§3.1 หมายเหตุ)

### `kernel/` — L0
- **Purpose:** event log (append-only, source of truth), tick scheduler, txn, seeded RNG, snapshot/compaction
- **Boundary:** broker ของ Phase 0 ต้องรับ event จาก kernel ได้โดยไม่แก้ retention/allocator
  → ตอนนี้ `Observation`/`TraceEvent` ทำหน้าที่เป็น placeholder ของ event log
- **Gate:** `replay_determinism` — replay ให้ event log เหมือนเดิม bit-for-bit
- **ของที่ต้องมาพร้อมกัน:** VCR pattern สำหรับ model output (§4.3 กฎ 5)

### `registry/` — L1
- **Purpose:** entity/component + schema versioning + upcasting on read
- **เชื่อมกับคำถามเปิด:** §13 ข้อ 6 (schema migration ของ belief)

### `cortex/` — L2
- **Purpose:** perceive → belief → candidate → score → decide → effect
- **คำถามที่ต้องตอบก่อน:** §13 ข้อ 1 — belief revision semantics (AGM / Bayesian / non-monotonic)
  **ต้องเลือกและ commit** ไม่งั้น belief revision จะกลายเป็น `dict[key] = value`
- **Gate:** `belief_causality` (§10) — perturb belief เดียว → decision เปลี่ยน และ trace ชี้กลับได้
- **ของเดิมที่รียูสได้:** Gate 10.6 chain จาก WSE prototype (รูป 2) เคยเดินครบถึง Decision Selection แล้ว

### `broker/` — L3 ★ (Phase 0 สร้างไปแล้วบางส่วน)
- Phase 0 ให้: retention, working set allocator, policy registry, OPT harness
- ที่ยังขาด: **consolidation cycle** (slow path §5.4), prospective memory registry, budget allocator หลาย agent
- **Gate:** `no_thrash` (§10)

### `modelbus/` — L4
- **Purpose:** HAL สำหรับ LLM + contract + fallback ladder + VCR record/replay
- **Boundary:** syscall boundary — LLM คืนได้แค่ proposal, kernel ตรวจแล้วค่อย apply (§4.3 กฎ 1)
- **Gate:** `degradation` — model bus ล่มทั้งหมด โลกยังเดินด้วย symbolic reflex ไม่มี state corruption
- **ของเดิมที่รียูสได้:** three-tier pre-execution fallback จาก SynaptaOS (§14)

### `trace/` — L5
- **Purpose:** causal lineage, replay, `soma.explain(decision_id)`
- **Gate:** `memory_causality` — ลบ memory ที่ explain ชี้ → decision ต้องเปลี่ยน
- **หมายเหตุ:** Langfuse ให้ observability ระดับ LLM call แต่ **state lineage ต้องสร้างเอง** (§14)

### `packs/social` — L6 pack แรก
- **Purpose:** social media simulation (§8) — pack ที่บีบทุก feature ของ SomaOS
- **Cost control:** ส่วนใหญ่ scroll/react = symbolic path ล้วน ไม่แตะ LLM (§8.2)
- **จริยธรรม:** simulation ล้วน ห้าม federate ห้ามแตะ platform จริง (§8.5, §8.6.4)
- **Demo หลัก:** rumor propagation / telephone-game distortion (§8.4)

### `packs/hr` — pack ที่สอง (thesis)
- **Purpose:** พิสูจน์ generality — kernel ต้องไม่ต้องแก้เพื่อรองรับ
- **ทำไมโดเมนนี้:** มี belief ที่ผิดเกี่ยวกับผู้สมัคร, revision จากคำตอบใหม่, scored candidate question,
  และ HR ต้องการคำอธิบายว่าทำไมถึงถามคำถามนั้น — ซึ่ง black-box LLM ให้ไม่ได้ (§9)

---

## คำถามเปิดที่ยัง deferred (§13) และจุดที่ต้องตอบ

| # | คำถาม | ต้องตอบก่อนเริ่ม |
|---|---|---|
| 1 | belief revision semantics | `cortex/` (Phase 2) |
| 2 | นิยาม τ | Phase 0 ใช้คงที่ (D-03) → adaptive เป็นของ Phase 1 |
| 3 | surprise ของ obs ที่ไม่มี predictor | ตอบแล้วใน D-04 (แยกเป็น novelty) |
| 4 | consolidation cadence | `broker/` consolidation (Phase 5) |
| 5 | multi-agent shared episodic | `packs/social` ตอนสเกล N (Phase 4) — **มีผลกับต้นทุนมหาศาล** |
| 6 | schema migration ของ belief | `registry/` (Phase 1) |
