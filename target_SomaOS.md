# target_SomaOS.md — v2

> **North-star document** ของโปรเจกต์ SomaOS — อ่านก่อนเขียนโค้ดทุกครั้ง
> Status: `RE-DESIGN / PRE-BUILD` — ยังไม่อนุมัติให้สร้าง full kernel
> Owner: Nin (Phatchradanai) · Last updated: 2026-08-28
>
> **v2 เขียนทับ v1 ทั้งฉบับ** — v1 ตั้งอยู่บนสมมติฐาน "surprise เป็นประตูตัดสินว่าจะเก็บหรือทิ้ง"
> ซึ่งถูกทดสอบแล้วว่าใช้ไม่ได้ **และขัดกับวิสัยทัศน์ของโปรเจกต์เอง**
> ผลการวัดของรอบนั้นเก็บไว้ที่ `plans/ARCHIVE_PHASE0_RESULT.md` (ตัวเลขยังจริง ดีไซน์ผิด)

---

## 0. TL;DR

SomaOS คือ **memory management runtime สำหรับ LLM agent** ที่ปฏิบัติกับ context window
เหมือน physical RAM และปฏิบัติกับคลังความจำภายนอกเหมือน disk ที่มีขนาดกำหนดได้

หลักการเดียวที่ต้องจำ:

> **ความจำไม่มีวันหาย — มันแค่ลึกลงและจางลง**
> **Context คือทรัพยากรที่ OS จัดสรร ไม่ใช่ที่ที่ความทรงจำอาศัยอยู่**

สิ่งที่ต่างจาก agent memory framework อื่น: **โครงสร้างของความจำถูกออกแบบ ไม่ใช่กองเวกเตอร์ก้อนเดียว**
— นิสัย, ความตั้งใจ, ทักษะ, ประสบการณ์ อยู่คนละที่ คนละกฎ คนละต้นทุน และการนึกคือ
**การเดินต้นไม้ที่มีต้นทุนจำกัด** ไม่ใช่การสแกนทุกอย่างที่เคยเจอ

---

## 1. เป้าหมายของเจ้าของโปรเจกต์

### 1.1 เชิงเทคนิค
- สร้าง **OS ที่จัดการ memory ให้ AI agent แบบสมองคน** — ความจำระยะยาวไม่สิ้นสุด โดย context ไม่บวม
- agent แต่ละตัวสะสม **นิสัย ความเชื่อ ประสบการณ์ ของตัวเอง** ที่ส่งผลต่อพฤติกรรมจริง
- **domain-agnostic** — kernel เดียว ใช้ได้หลายโดเมนผ่าน "pack"
- **production-grade**: replay ได้, observability, failure mode ชัด, cost governance
- ฐานเป็น **CS/CE** (OS theory, DB theory, information retrieval) ไม่ใช่ prompt engineering
- **รันบน microcontroller ได้** — นี่คือเหตุผลสำคัญที่ทำให้ OS นี้มีประโยชน์จริง ไม่ใช่ผลพลอยได้
  บนเครื่องทั่วไปที่ดิสก์เหลือเฟือ กลไกเจือจางแทบไม่ทำงาน แต่บนชิปที่ flash เป็น MB และ SRAM เป็น KB
  มันคือสิ่งที่ทำให้ระบบเป็นไปได้เลย — ดู `plans/05_EMBEDDED_TARGET.md`

### 1.2 เชิงยุทธศาสตร์
- เป็น **backbone ที่ใช้ซ้ำได้** ข้ามโปรเจกต์: SynaptaOS (smart home), thesis (HR interview agent), BCI/gaming
- เป็นสินทรัพย์เชิงเทคนิคที่ต่อยอดเป็นธุรกิจได้
- มี **defensible differentiation** ไม่ใช่ wrapper บาง ๆ

### 1.3 สิ่งที่ *ไม่ใช่* เป้าหมาย
- ไม่ได้จะทำ RAG library อีกตัว
- ไม่ได้จะพิสูจน์ว่าแนวคิดนี้ใหม่ (มันไม่ใหม่ และเรารู้)
- ไม่ได้จะสร้าง general framework ก่อนมี use case จริง

---

## 2. Thesis ของโปรเจกต์ (เขียนใหม่ทั้งข้อ)

> ### ข้อเสนอที่ต้องพิสูจน์
>
> ถ้าออกแบบ **โครงสร้างหน่วยความจำ** ให้ถูกต้อง — แยกตามหน้าที่, ค้นด้วยการเดินต้นไม้บนเวกเตอร์,
> และเสื่อมสภาพอย่างค่อยเป็นค่อยไปเมื่อคลังเต็ม — agent จะได้:
>
> 1. **ความจำที่โตไม่สิ้นสุด** โดย **context คงที่**
> 2. **ต้นทุนการนึกที่โตแบบ sub-linear** เทียบกับขนาดคลัง
> 3. **การเสื่อมแบบมนุษย์**: ลืมรายละเอียด แต่แก่นยังอยู่ — ไม่ใช่หน้าผา
> 4. **ตัวตนที่นิ่ง** ข้าม session ยาว ๆ และ **อธิบายได้ว่าความจำชิ้นไหนทำให้เกิดพฤติกรรมไหน**
>
> ถ้าข้อ 1–3 ผิด — โปรเจกต์นี้ไม่ควรถูกสร้าง

### ⚠️ สิ่งที่เปลี่ยนจาก v1 และเหตุผล

v1 อ้างว่า *"policy แบบ deterministic ตัดสินใจแทน LLM"* คือ moat **ข้ออ้างนั้นถูกถอนแล้ว**

| | v1 | v2 |
|---|---|---|
| ใครเลือกว่าจะนึกถึงอะไร | policy ล้วน ห้าม LLM แตะ | **agent เลือกเอง** ผ่าน transition tool |
| ทำไมเปลี่ยน | — | ถ้าไม่ให้เลือกเอง มันก็ไม่เหมือนคน ซึ่งเป็นเป้าหมายทั้งหมด |
| แล้ว determinism หายไหม | — | **ไม่หาย ย้ายที่** — กลไกยัง deterministic, replay ด้วย VCR ยังตรงเป๊ะ (§7 M7) |
| moat ย้ายไปไหน | ความ deterministic | **โครงสร้างความจำ + เส้นโค้งการเสื่อม + prospective + explainability** |

**ของแถมที่ได้จากการยอมให้ LLM อยู่ในลูป:** เราวัดกับ LoCoMo/LongMemEval เทียบ MemGPT/Mem0/Zep
ได้เป็นครั้งแรก — v1 ทำไม่ได้เลยเพราะบังคับว่าห้ามมี LLM

---

## 3. ตำแหน่งเทียบกับงานที่มีอยู่

### 3.1 Prior art
| งาน | สิ่งที่เอามาใช้ |
|---|---|
| Denning, Working Set Model (1968) | นิยาม short-term memory เชิงรูปนัย + ทฤษฎี thrashing |
| Belady's OPT / MIN | oracle baseline (เป้าเปลี่ยนเป็น "จัดสรรคลัง" ดู §7) |
| Multi-level page table + TLB | **ต้นแบบตรง ๆ ของ memory address tree** |
| Splay tree / LRU | กลไก "ยิ่งใช้ยิ่งตื้น ยิ่งไม่ใช้ยิ่งลึก" |
| Merkle tree (blockchain) | content addressing, dedupe, ตรวจ integrity ทั้งต้นไม้ |
| Product quantization / IVF / HNSW | บันไดการบีบอัดเวกเตอร์ + การค้นแบบเดินลำดับชั้น |
| Event Sourcing + CQRS | event log เป็น source of truth, state เป็น projection |
| BDI — Rao & Georgeff (1995) | belief–desire–intention loop |
| AGM belief revision | semantics ของการแก้ belief |
| Generative Agents — Park et al. (2023) | memory stream + reflection |
| MemGPT / Letta (2023) | virtual memory metaphor — **และคือคู่แข่งตรงที่สุดตอนนี้** |
| Mem0 / Zep / LangMem | extraction + temporal knowledge graph |
| LoCoMo, LongMemEval | benchmark ความจำระยะยาว |

> ⚠️ วงการนี้ขยับเร็ว — ต้อง survey ซ้ำก่อนลงทุนหนัก โดยเฉพาะ MemGPT/Letta และ benchmark ใหม่

### 3.2 ช่องว่างที่ SomaOS ปักธง (แก้ไขจาก v1)

| # | ช่องว่าง | สถานะ | moat |
|---|---|---|---|
| 1 | **คลังมีขนาดกำหนดได้ + เสื่อมอย่างค่อยเป็นค่อยไป** | ใหม่ใน v2 | **สูงสุด** |
| 2 | **โครงสร้างแยกตามหน้าที่** (นิสัย/trigger/ทักษะ/ประสบการณ์) | ใหม่ใน v2 | **สูงสุด** |
| 3 | **Prospective memory** (interrupt ไม่ใช่ retrieval) | ยกมาจาก v1 | **สูงสุด** |
| 4 | **Causal trace: memory → decision** (ได้ฟรีจากเส้นทางการเดิน) | ยกมาจาก v1 | **สูงสุด** |
| 5 | การค้นแบบเดินต้นไม้ที่มีเพดานต้นทุน | ใหม่ใน v2 | สูง |
| 6 | Tick loop | ยกมาจาก v1 | สูง |
| 7 | ~~Surprise-gated encoding~~ | ❌ **ถอนแล้ว** — ทดสอบแล้วใช้ไม่ได้ | — |
| 8 | ~~Policy-driven (ไม่ใช้ LLM)~~ | ❌ **ถอนแล้ว** — ขัดกับเป้าหมาย | — |

---

## 4. สถาปัตยกรรม

### 4.1 Mapping หลัก

| OS concept | SomaOS |
|---|---|
| Physical RAM | context window (fixed, แพง) |
| Disk ที่มีขนาดจำกัด | **memory store — `store_budget_bytes` = "ขนาดสมอง"** |
| **Multi-level page table** | **memory address tree** |
| **TLB** | cache ของ address ที่เพิ่ง resolve |
| **Page compression / zswap** | **บันไดการเจือจาง D0→D4** |
| **Interrupt vector table** | **TRIGGER region (prospective memory)** |
| Pinned kernel pages | **CORE region (นิสัย/ตัวตน)** |
| Page fault | retrieval miss → ต้องเดินลงไปเอา |
| Working set `W(t, τ)` | สิ่งที่อยู่ใน context ตอนนี้ |
| Thrashing | context churn จน task ไม่คืบ |
| Process | agent |
| Scheduler quantum | tick |
| Device driver / HAL | model bus |

### 4.2 Layer stack

```
┌─────────────────────────────────────────────────────────┐
│  L6  SOMA PACKS        userland: domain-specific         │
├─────────────────────────────────────────────────────────┤
│  L5  SOMA TRACE        causal lineage · replay · XAI     │
├─────────────────────────────────────────────────────────┤
│  L4  SOMA MODEL BUS    HAL for LLM · contract · fallback │
│  ═════════ SYSCALL BOUNDARY ══════════════════════════   │
├─────────────────────────────────────────────────────────┤
│  L3  SOMA BROKER  ★    memory tree · address resolution  │
│                        dilution · budget · recall FSM    │
├─────────────────────────────────────────────────────────┤
│  L2  SOMA CORTEX       perceive→belief→candidate→decide   │
├─────────────────────────────────────────────────────────┤
│  L1  SOMA REGISTRY     entity/component · schema version │
├─────────────────────────────────────────────────────────┤
│  L0  SOMA KERNEL       event log · tick · txn · RNG      │
└─────────────────────────────────────────────────────────┘

★ = หัวใจของโปรเจกต์ ที่เหลือมีไว้รับใช้มัน
```

### 4.3 กฎเหล็กของ kernel

1. **LLM ไม่เคยเขียน state โดยตรง** — คืนได้แค่ proposal, kernel ตรวจ schema + invariant แล้วค่อย apply
   → **ยังบังคับใช้เต็มที่แม้ agent จะเลือก transition เองได้** — agent เลือก *ทิศ* engine เป็นคน *เดิน*
2. **Event log คือ source of truth เดียว** — state ทุกตัวเป็น materialized projection
3. **ไม่มีการทำลายความทรงจำ** — บีบอัดได้ เจือจางได้ แต่ address ต้อง resolve ได้เสมอ
4. **RNG ต้อง seeded แยก stream ต่อ entity**
5. **บันทึก model output ลง event log (VCR)** — replay ใช้ของที่บันทึกไว้ → deterministic 100%
6. **Tick loop ห้าม block รอ LLM** — เกิน budget ให้ fast path ยิงก่อน ผลที่มาทีหลังเข้าเป็น revision event
7. **ไม่มี `write()` API สำหรับ memory** — ทุกอย่างเข้าผ่าน `perceive()`
8. **engine ตัดสินใจจากเวกเตอร์เท่านั้น** — ข้อความเป็นเงาสำหรับมนุษย์และเป็น payload ตอนส่งเข้า context

---

## 5. Memory model

> **สเปกเต็มอยู่ที่ `plans/03_MEMORY_ARCHITECTURE.md`** — หัวข้อนี้เป็นบทสรุป

### 5.1 สี่ภูมิภาค

| ภูมิภาค | เก็บอะไร | เข้า context ยังไง | เจือจางได้ถึง |
|---|---|---|---|
| `CORE` | ตัวตน · นิสัย · ค่านิยม · วิธีพูด | **อยู่ทุกครั้ง ไม่ต้องเรียก** | ❌ ห้าม |
| `TRIGGER` | กี่โมงทำอะไร · เกิด event อะไรทำอะไร | ไม่กิน context — เป็น interrupt | ❌ ห้าม |
| `SKILL` | วิธีทำ · เคยทำแล้วได้ผล | ดึงเมื่อสถานการณ์ตรง | D2 |
| `ARCHIVE` | ประสบการณ์ · เหตุการณ์ · ความรู้ | ต้องเรียกถึงจะมา | D4 |

### 5.2 ความจำเก็บเป็นเวกเตอร์ ไม่ใช่ข้อความ

- `vec` = **ความรู้จริงของระบบ** ทุกการเปรียบเทียบ ยุบรวม เจือจาง เกิดบนนี้
- `text_ref` = **เงา** ให้คนอ่านเข้าใจว่าเก็บอะไรไว้ (debug/วิจัย) และเป็น payload ตอน materialize
- บังคับด้วย invariant: **ลบข้อความทั้งคลัง → เส้นทางการเดินต้องเหมือนเดิม bit-for-bit**
- ผลพลอยได้: ข้ามภาษาได้ ข้ามสื่อได้ (ภาพ/เสียงในอนาคตใช้โครงเดิม)

### 5.3 การเสื่อม — สองแกนที่แยกกัน

| แกน | ขับด้วย | ผลที่รู้สึก |
|---|---|---|
| **ความลึก** — ต้องเดินกี่ก้าว | ความถี่การใช้ (splay tree) | นึกออกช้าลง |
| **ความคมชัด** — เหลือรายละเอียดแค่ไหน | คลังเต็ม (บันได D0→D4) | จำแก่นได้ จำรายละเอียดไม่ได้ |

**ไม่มีขั้นที่แปลว่า "หายไป"** — D4 คือพื้น เหลืออย่างน้อย "เคยมีเรื่องแบบนี้อยู่"

### 5.4 Two-speed architecture

**Fast path — ทุก tick ไม่แตะ LLM**
`CORE` ที่ resident อยู่แล้ว · เช็ค `TRIGGER` แบบ O(1) · greedy descend ตาม cosine
→ ต้อง deterministic และเป็นทางที่ระบบใช้เมื่อ model bus ล่ม

**Slow path — Consolidation Cycle ("การนอน") แตะ LLM ได้**
ยุบเหตุการณ์ซ้ำเป็นความรู้ · ตกผลึกนิสัยขึ้น `CORE` · จัดโครงต้นไม้ · บังคับ `store_budget`
→ เรียก LLM ครั้งเดียวต่อหลายพัน event

### 5.5 การนึกคือ state machine

```
IDLE → CUE → { RESIDENT (O(1))  |  NAVIGATE ⇄ (descend/ascend/lateral) } → MATERIALIZE → SETTLE
```
agent เลือก transition ได้เองผ่าน tool · engine บังคับเพดาน `recall_ops_budget`
· **ทุกก้าวถูกบันทึก → `explain()` ได้ฟรี**

### 5.6 สิ่งที่ห้ามลอกจากสมอง

| ห้ามลอก | ทำแทนด้วย |
|---|---|
| Reconstructive recall ที่ทำลายของเดิม | เจือจางเป็นทางเดียว มี provenance กลับ raw event เสมอ |
| การลืมแบบ decay จนหายเกลี้ยง | เสื่อมถึง D4 แล้วหยุด + log ว่าอะไรเจือจางเพราะอะไร |
| ให้ neuroscience กำหนด guarantee | guarantee มาจาก OS/DB/IR theory |

---

## 6. Syscall interface (ร่าง)

```python
soma.perceive(obs) -> EncodeReceipt       # เก็บเสมอ ไม่มีการทิ้ง — คืน address
soma.recall(cue, budget, mode) -> ContextBundle
soma.walk(addr, move) -> WalkResult       # descend | ascend | lateral — tool ของ agent
soma.resolve(addr) -> (node, fidelity)    # ไม่เคยคืน None
soma.pin(addr, ttl)                       # บังคับให้อยู่ใน context
soma.intend(trigger, action)              # prospective memory
soma.consolidate(window)                  # slow path, LLM eligible
soma.explain(decision_id) -> Lineage      # เส้นทางที่เดิน
```

**ไม่มี `soma.write()` และไม่มี `soma.forget()` โดยเจตนา** — ไม่มีใครลบความทรงจำได้ แม้แต่ตัวระบบเอง

### Data contract หลัก

```python
MemoryNode    { addr, region, level, vec, dtype, dim, fidelity,
                parent, children, n_merged, span, keys, stat, text_ref, raw_refs }
Alias         { addr_old -> addr_now }                      # append-only
Trigger       { key, kind(time|event|predicate), condition, action, state, ttl }
ContextBundle { bundle_hash, budget, zones{core,knowledge,episode}, items[], cache_key }
WalkPath      { steps[(move, addr, score)], ops_used, stopped_by }
Decision      { id, tick, agent, chosen, bundle_hash, walk_path, model_call_id? }
```

**`ContextBundle` ต้องมี zone และบังคับ static-before-dynamic ที่ระดับ API** —
`CORE` อยู่หัวเสมอเพื่อรักษา prefix cache เป็นกฎของ broker ไม่ใช่สิ่งที่ pack author ต้องจำ

---

## 7. Research spike — Phase 0b

> **ห้ามสร้าง kernel เต็มก่อนผ่าน Phase 0b**

### 7.1 สิ่งที่ต้องสร้าง (แค่ 4 ชิ้น)

1. **Memory tree store** — content-addressed, alias table, splay promote/sink
2. **Dilution engine** — บันได D0→D4 บังคับ `store_budget_bytes` แบบ deterministic
3. **Recall FSM** — เดินต้นไม้ มีเพดาน ops บันทึกเส้นทาง + fast path ที่ไม่แตะ LLM
4. **Bench harness ที่ปรับจากของเดิม** — เพิ่มแกน `store_budget` และคะแนนแบบมีระดับ

**ลำดับ:** โครงสร้าง → เจือจาง → เดิน → วัด **ยังไม่มี LLM ในขั้นนี้** (LLM เข้าที่ Phase 0.5 ตอนวัด M8)
เหตุผลที่ยังไม่ใส่ LLM: เรากำลังทดสอบ *โครงสร้าง* ไม่ใช่ *คุณภาพการเลือกของโมเดล*
fast path ต้องพิสูจน์ตัวเองก่อน แล้ว escalation ค่อยพิสูจน์ว่าเพิ่มค่าได้จริงเท่าไหร่

### 7.2 Baseline ที่ต้องเอาชนะ (ทุกตัวอยู่ใต้ `store_budget` เท่ากัน)

| id | policy | บทบาท |
|---|---|---|
| B0 | full context | upper bound คุณภาพ / upper bound cost |
| B1 | sliding window last-K | baseline ที่ง่ายที่สุด |
| B2 | flat vector RAG top-k | **baseline ที่คนใช้จริง — คู่แข่งหลัก** |
| B2c | flat RAG + บีบอัดแบบสุ่มเมื่อคลังเต็ม | แยกผลของ *โครงสร้าง* ออกจากผลของ *การบีบอัด* |
| B4 | LLM-managed paging (MemGPT-style) | คู่แข่งตัวจริง |
| **S** | **SomaOS memory tree** | ของเรา |
| OPT | oracle รู้อนาคต (จัดสรรคลังที่ดีที่สุด) | upper bound เชิงทฤษฎี |

> **B2c สำคัญมาก** — ถ้าไม่มี เราจะแยกไม่ออกว่าที่ชนะเพราะ "ต้นไม้" หรือแค่เพราะ "บีบอัดเป็น"

### 7.3 Metrics — ดู `plans/03_MEMORY_ARCHITECTURE.md` §6

`M1` เส้นโค้งความจุ ★ · `M2` เส้นโค้ง context · `M3` ต้นทุนการนึก · `M4` ความเสถียรของตัวตน
· `M5` ความแม่นของ trigger · `M6` อธิบายได้ · `M7` replay ด้วย VCR · `M8` เทียบ LoCoMo/LongMemEval

### 7.4 Kill criteria — เงื่อนไขที่ต้องหยุด (เขียนใหม่ทั้งชุด)

หยุดทันทีถ้า **ข้อใดข้อหนึ่ง** เป็นจริงหลัง Phase 0b:

- **KC1 — โครงสร้างไม่ช่วย:** `S` ไม่ชนะ `B2c` อย่างมีนัยสำคัญ **ที่ `store_budget` เท่ากัน**
  (ถ้าแพ้ `B2c` แปลว่าที่ได้มาคือผลของการบีบอัด ไม่ใช่ของโครงสร้าง → ต้นไม้ไม่มีค่า)
- **KC2 — เสื่อมเป็นหน้าผา:** เมื่อลด `store_budget` ลง 10 เท่า **คะแนนแก่นตกเกิน 30%**
  (ถ้าตกพร้อมรายละเอียด แปลว่าไม่ได้ "ลืมรายละเอียดแต่พอจำได้" — วิสัยทัศน์หลักผิด)
- **KC3 — นึกไม่ sub-linear:** `recall_ops` โตเกิน `O(√N)` เมื่อคลังโตจาก 10³ → 10⁶
  (ถ้าโตเชิงเส้น การเดินต้นไม้ก็ไม่ต่างจากการสแกน)
- **KC4 — context ไม่คงที่:** ต้องเพิ่ม `context_budget` ตามขนาดคลังเพื่อรักษาคุณภาพ
  (ขัดกับข้ออ้างหลักข้อ 1 โดยตรง)
- **KC5 — ตัวตนไม่นิ่ง:** อัตราขัดแย้งกับ `CORE` ไม่ต่ำกว่า baseline อย่างมีนัยสำคัญ

> **ทุกเกณฑ์ต้อง pre-register ก่อนรัน และวัดบน seed ชุดใหม่**
> seed holdout ชุดเดิมถูกเผาไปแล้ว (`plans/ARCHIVE_PHASE0_RESULT.md`) ห้ามใช้ซ้ำ
>
> เสียเวลาสองสัปดาห์ ดีกว่าเสียหกเดือน

---

## 8. Pack แรก: Social Media Simulation (`soma-pack-social`)

### 8.1 ทำไมโดเมนนี้
ต้นทุนต่ำสุด (text-native ล้วน) · เห็นภาพสุด (emergent social phenomena) · **มี ground truth 100%**
(เรา generate โลกเอง) · บีบทุก feature ของ SomaOS · สเกลจาก 10 → 200 agent ได้ทันที

### 8.2 โครงโลกจำลอง
```
World  : feed timeline, topic space, ground-truth event stream
Agent  : persona (CORE) + belief + social graph + memory tree
Action : scroll · react · comment · post · share · DM · mute · ignore
Tick   : 1 tick = ช่วงเวลาในวัน (เช้า/กลางวัน/เย็น/ดึก)
Tier   : FOCUS (แตะ LLM ได้) / AMBIENT (symbolic) / DORMANT (aggregate)
```
**คุมต้นทุน:** agent ส่วนใหญ่ต่อ tick แค่ scroll/react → symbolic ล้วน ไม่แตะ LLM

### 8.3 Feature ถูกทดสอบยังไง

| Claim | ทดสอบด้วยอะไร |
|---|---|
| คลังมีขนาด + เสื่อมค่อยเป็นค่อยไป | ตั้ง `store_budget` ต่างกัน → agent "ความจำดี" กับ "ความจำไม่ดี" ต่างกันจริงไหม |
| นิสัยอยู่ใน `CORE` เสมอ | persona consistency ข้าม session ยาว |
| Prospective memory | agent ทำตามที่ประกาศไว้จริงไหม ("เดี๋ยวจะไปเถียงต่อ") |
| การเดินต้นไม้ | `recall_ops` ตอน N โตขึ้น |
| Causal trace | ตอบได้ไหมว่า "ทำไม A ถึงตอบ B แบบมีอารมณ์" |
| Cost sublinearity | `tokens_per_agent_day` ตอน N = 10 → 50 → 200 |
| Consolidation | หลัง sleep cycle agent สรุป "คนนี้เป็นคนแบบไหน" ได้ถูกไหม |

### 8.4 Emergent phenomena ที่จะวัด
Rumor propagation & telephone-game distortion (demo ที่ทรงพลังที่สุด — และตอนนี้มีคำอธิบายเชิงกลไก:
**ข่าวลือเพี้ยนเพราะมันถูกเจือจางลงบันได D**) · opinion clustering · parasocial asymmetry ·
memory decay ในความสัมพันธ์

### 8.5 ข้อควรระวังเชิงจริยธรรม
- **simulation ล้วน** — ไม่แตะ platform จริง ไม่ดึงข้อมูลผู้ใช้จริง ไม่สร้างบัญชีจริง
- persona ทั้งหมด synthetic ไม่อ้างอิงบุคคลจริง
- ถ้าเผยแพร่ ต้องระบุชัดว่าเป็นโลกจำลอง
- **ห้าม** พัฒนาไปทาง astroturfing / bot network บน platform จริง

### 8.6 Validation Ladder
```
L1  synthetic trace (Phase 0b)     → เส้นโค้งความจุ + competitive ratio
L2  LoCoMo / LongMemEval           → เทียบ MemGPT/Mem0/Zep ด้วยตัวเลขมาตรฐาน
L3  replay corpus สาธารณะ           → ทนต่อ distribution จริงไหม
L4  Mastodon instance ของตัวเอง    → API จริง protocol จริง ground truth ของเรา (ปิด federation)
L5  human evaluation               → ความสมจริงเชิงคุณภาพ
─────────────────────────────────────────────────────────────
    live public platform           → ไม่อยู่ใน roadmap (ไม่มี ground truth + ผิด ToS)
```
> ⚠️ L1 คือขั้นเดียวที่คำนวณ `competitive_ratio` ได้จริง เพราะ trace จบแล้วและรู้อนาคตทั้งหมด
> ⚠️ ห้ามเปิด federation ไปยัง instance สาธารณะเด็ดขาด (ข้ามเส้นเดียวกับ §8.5)

---

## 9. Roadmap

| Phase | ผลลัพธ์ | Gate | Ladder |
|---|---|---|---|
| **0b** | memory tree + dilution + recall FSM + bench | KC1–KC5 ผ่านทั้งหมด (§7.4) | L1 |
| **0.5** | รันบน LoCoMo / LongMemEval + เปิด escalation ให้ agent เลือกเอง | เทียบ MemGPT/Mem0/Zep ได้ และ escalation เพิ่มค่าได้จริง | L2 |
| **1** | Kernel ขั้นต่ำ: event log + tick + RNG + replay + VCR | `replay_determinism` ผ่าน bit-for-bit | — |
| **2** | Cortex: perceive → belief → decide | `belief_causality` ผ่าน | — |
| **3** | `soma-pack-social` 10 agents, LLM in loop | คุณภาพ ≥ baseline ที่ต้นทุน ≤ 40% | L3 |
| **4** | สเกล 10 → 50 → 200 บน Mastodon harness | `tokens_per_agent_day` โตต่ำกว่าเชิงเส้น + `degradation` ผ่าน | L4 |
| **5** | Consolidation cycle + prospective memory เต็มรูป | ทำตามเจตนาที่ประกาศ ≥ 80% | L5 |
| **6** | Pack ที่สอง (thesis HR interview) | kernel ไม่ต้องแก้เพื่อรองรับ | — |

---

## 10. Conformance Gates (CI-runnable)

```
GATE memory_never_lost:
  GIVEN address ใด ๆ ที่เคยถูกสร้าง
  ASSERT resolve(addr) คืนค่าเสมอ ไม่ว่าคลังจะถูกบีบไปกี่รอบ
  ASSERT fidelity ลดลงอย่างเดียว ไม่เคยเพิ่ม

GATE vector_authority:
  GIVEN คลังที่ถูกลบ text_ref ทั้งหมด
  ASSERT เส้นทางการเดินและ node ที่ถูกเลือก เหมือนเดิม bit-for-bit

GATE identity_stable:
  GIVEN session ยาวจนคลังเต็มหลายรอบ
  ASSERT CORE และ TRIGGER ไม่เคยถูกเจือจางหรือถูกไล่

GATE sublinear_recall:
  GIVEN คลังขนาด 10^3 → 10^6
  ASSERT recall_ops โตช้ากว่า O(√N)

GATE memory_causality:
  GIVEN decision D
  ASSERT explain(D) คืนเส้นทางที่ระบุ node ได้
  ASSERT ถ้าลบ node นั้น decision เปลี่ยน

GATE replay_determinism:
  GIVEN session ที่มี LLM call (บันทึกด้วย VCR)
  ASSERT replay ให้ event log เหมือนเดิม bit-for-bit

GATE degradation:
  GIVEN model bus ล่มทั้งหมด
  ASSERT fast path ยังเดินต้นไม้ได้ โลกยังเดินต่อ ไม่มี state corruption

GATE no_thrash:
  GIVEN budget ≥ working_set(task)
  ASSERT context_churn_rate ต่ำกว่าเกณฑ์
```

**Gate suite นี้คือ differentiator ที่แท้จริง** — ไม่มี framework เจ้าไหนทดสอบสมบัติเหล่านี้

---

## 11. Repo layout

```
somaos/
├── kernel/           # L0
├── registry/         # L1
├── cortex/           # L2
├── broker/       ★   # L3
│   ├── memory/       #     tree · node · address · alias
│   ├── dilution/     #     บันได D0→D4 · budget enforcement
│   ├── recall/       #     FSM · walk · fast path
│   ├── regions/      #     core · trigger · skill · archive
│   ├── policies/     #     B0..B4 + S — สลับได้ผ่าน config
│   └── opt/          #     oracle harness
├── modelbus/         # L4
├── trace/            # L5
├── packs/
├── gates/            # conformance suite
└── bench/            # metrics, baselines, report generator
```

**Phase 0b แตะแค่ `broker/`, `bench/`, `tests/`** ที่เหลือยังไม่ต้องมี

---

## 12. Non-goals และ scope guard

- ❌ ห้ามสร้าง general framework ก่อนมี pack ที่ใช้งานจริงอย่างน้อย 1 ตัว
- ❌ **ห้ามลบความทรงจำ ไม่ว่ากรณีใด** — บีบอัดได้ เจือจางได้ ลบไม่ได้
- ❌ **ห้ามให้ engine อ่านข้อความเพื่อตัดสินใจ** — เวกเตอร์เท่านั้น
- ❌ **ห้ามมี code path ที่ O(N) ต่อการนึกหนึ่งครั้ง**
- ❌ ห้ามให้ LLM เขียน state โดยตรง (proposal เท่านั้น)
- ❌ ห้ามทำ UI/frontend ก่อน Phase 3
- ❌ ห้าม optimize ก่อนมีตัวเลขจาก bench
- ❌ ห้ามเพิ่ม dependency นอก stdlib + numpy โดยไม่ถาม
- ⚠️ คำว่า "OS" เป็นข้อจำกัดการออกแบบ ไม่ใช่การตลาด — ถ้าอันไหนไม่ตรง metaphor OS ให้ตั้งคำถามกับมัน

---

## 13. คำถามเปิดที่ยังไม่มีคำตอบ

1. **embedding ของ Phase 0b มาจากไหน** — hash-based deterministic ที่สร้างเอง หรือของจริง?
   (แนะนำแบบแรก เพราะกำลังทดสอบโครงสร้าง ไม่ใช่คุณภาพ embedding — แต่ interface ต้องสลับได้)
2. **branching factor และเกณฑ์แตก/ยุบ node ของต้นไม้**
3. **`CORE` เริ่มต้นมาจากไหน** — เขียนมือตอนสร้าง agent แล้วให้ตกผลึกเพิ่มเอง?
4. **`SKILL` ควรแยกจาก `CORE` จริงไหม**
5. **Belief revision semantics** — AGM / Bayesian / non-monotonic? (ต้องตอบก่อน `cortex/`)
6. **Consolidation ควรรันบ่อยแค่ไหน**
7. **Multi-agent shared memory** — สอง agent ที่อยู่เหตุการณ์เดียวกัน แชร์ node หรือมีสำเนา?
   (content-addressing ทำให้แชร์ได้ฟรี — แต่ต้องคิดเรื่อง privacy/ตัวตน)
8. **Schema migration ของ node** ตอนโลกรันมาเป็นเดือน

---

## 14. Assets ที่มีอยู่แล้วและควรรียูส

| ของเดิม | ใช้ที่ไหน |
|---|---|
| Bench harness ของ Phase 0 (runner, report, gate, seed split) | **ใช้ต่อได้เกือบทั้งหมด** — ปรับแค่แกนและ metric |
| OPT oracle (Belady, ยืนยันกับ brute force แล้ว) | เปลี่ยนเป้าเป็น "จัดสรรคลัง" แล้วใช้ต่อ |
| Determinism contract (seeded stream, canonical hashing) | ใช้ต่อทั้งดุ้น |
| Working set allocator | ฝั่ง context ไม่เปลี่ยน ใช้ต่อได้ |
| Selective Schema Injection (SynaptaOS) | ต้นแบบของ zone layout |
| GraphRAG / knowledge graph (SynaptaOS) | ต้นแบบของ lateral link ใน `ARCHIVE` |
| Three-tier pre-execution fallback | fallback ladder ระดับ kernel |
| Static-before-dynamic prompt zoning | บังคับเป็นกฎของ `ContextBundle` |
| Langfuse | observability ระดับ LLM call — state lineage ต้องสร้างเอง |

---

## 15. คำสั่งสำหรับ Claude Code

1. อ่านไฟล์นี้ + `plans/03_MEMORY_ARCHITECTURE.md` ก่อนเสมอ ถ้าคำขอขัด §12 ให้ทักท้วงก่อนทำ
2. **งานปัจจุบันคือ Phase 0b เท่านั้น** — แตะได้แค่ `broker/`, `bench/`, `tests/`
3. ทุก policy implement interface เดียวกัน สลับผ่าน config เพื่อ benchmark เทียบตรง ๆ
4. เขียน test ก่อนเสมอสำหรับ invariant ใน `03_MEMORY_ARCHITECTURE.md` §7
5. dependency: stdlib + numpy เท่านั้น (numpy จำเป็นแล้วเพราะทุกอย่างเป็นเวกเตอร์)
6. metric ทุกตัว export เป็น structured data (JSONL) ห้าม print
7. **ถ้า kill criteria §7.4 เป็นจริง — บอกตรง ๆ อย่าหาทางแก้ตัวเลข** (รอบที่แล้วทำถูกแล้ว ทำแบบนั้นต่อ)
8. ห้าม tune แล้ววัดบน holdout ซ้ำ — tune บน dev, วัด holdout ครั้งเดียว, pre-register ก่อนรัน
