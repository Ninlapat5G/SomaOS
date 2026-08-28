# plan.md — แผนงานและสถานะ

> อัปเดตล่าสุด: 2026-08-28 — **Phase 0b: A0–A7 เสร็จ + integration · 539 tests ผ่าน**
> 📄 สรุปสถาปัตยกรรมสำหรับตรวจงาน: https://claude.ai/code/artifact/fb680222-48a4-4c3f-8c17-d7a6331deb47
> ดีไซน์เก่า (surprise-gated encoding) ถูกยกเลิก — ผลการวัดเก็บที่ `plans/ARCHIVE_PHASE0_RESULT.md`

---

## 0. สรุปสั้นที่สุด

Phase 0 รอบแรกทดสอบกลไกที่ **ขัดกับวิสัยทัศน์ของโปรเจกต์เอง** (`S` ทิ้งข้อมูลถาวร 75–88%
ทั้งที่เป้าหมายคือความจำไม่สิ้นสุด) gate FAIL ทั้ง 4 ข้อ และตอนนี้รู้แล้วว่าไม่ใช่เพราะ tune ไม่ดี

**ดีไซน์ใหม่:**
- ความจำ**ไม่มีวันหาย** — "ลืม" = ลึกลง (นึกออกช้าลง) + จางลง (รายละเอียดหาย แก่นอยู่)
- เก็บเป็น **เวกเตอร์** (ความรู้ของ OS) + ข้อความอ้างอิง (ให้คนอ่าน debug/วิจัย)
- **คลังมีขนาดกำหนดได้** = "ขนาดสมอง" ของ agent แต่ละตัว
- แยก **4 ภูมิภาค**: `CORE` (นิสัย อยู่ทุกครั้ง) · `TRIGGER` (interrupt) · `SKILL` · `ARCHIVE`
- การนึก = **เดินต้นไม้แบบ state machine** ที่ agent เลือกทิศเองได้ engine เป็นคนเดินและคุมเพดาน
- **การวัดเปลี่ยนทิศทั้งหมด** — เลิกวัด "ตัดสินใจถูกไหม" หันไปวัด **เส้นโค้งความสามารถ** ของโครงสร้าง

---

## 1. สถานะ

### 1.1 เอกสาร
- [x] `plans/ARCHIVE_PHASE0_RESULT.md` — เก็บผลการวัดของรอบเก่าไว้เป็นหลักฐาน
- [x] `target_SomaOS.md` **v2** — เขียนทับทั้งฉบับ (thesis, moat, memory model, kill criteria ใหม่)
- [x] `plans/03_MEMORY_ARCHITECTURE.md` **v2.1** — สเปกเต็ม + แก้บันไดตามผลวัดจริง
- [x] `plans/04_HUMAN_MEMORY_BASIS.md` — พื้นฐานจากงานวิจัยความจำมนุษย์ → โครงสร้าง tree
- [x] `plans/01_DECISIONS.md` — N-01..N-15 แทนที่ D-01..D-14 ทั้งชุด
- [x] `plans/02_INTERFACES.md` — เขียนใหม่จากโค้ดที่สร้างจริงแล้ว
- [x] `CLAUDE.md` — อัปเดตสถานะและกฎ
- [ ] `plans/00_PHASE0_MASTER_PLAN.md` — ยังเป็นของเก่า ต้องเขียนใหม่เป็น master plan ของ 0b

### 1.2 โค้ดใหม่ที่สร้างแล้ว ✅

| WP | โมดูล | test | สาระ |
|---|---|---|---|
| **A1** | `broker/memory/{vector,address,node}.py` | 34 | เวกเตอร์ D0–D2 · content address · alias table |
| **A2** | `broker/dilution/engine.py` | 24 | บันได D0→D4 · บังคับ `store_budget_bytes` · audit log |
| **A3** | `broker/memory/tree.py` | 28 | ต้นไม้ · แกนความลึก (retrieval strength) · เดินแบบมีเพดาน |
| **A4** | `broker/regions/{core,trigger}.py` | 27 | CORE resident + quota · TRIGGER FSM 3 ชนิด |
| **A5** | `broker/recall/machine.py` | 28 | FSM 6 สถานะ · 5 moves · WalkPath · fast path |
| **A7** | `broker/consolidation/machine.py` | 23 | REPLAY→ABSTRACT→REBALANCE→ENFORCE · ตกผลึกนิสัย |
| — | `tests/test_integration_agent_life.py` | 12 | รัน 5 โมดูลด้วยกัน 200 วัน — **เจอบั๊ก 4 ตัวที่ unit test มองไม่เห็น** |
| — | `bench/experiments/` | — | `quantization_fidelity.py` · `capacity_curve.py` |

**รวม 539 tests ผ่านทั้งหมด**

### 1.3 โค้ดเก่าที่ยังไม่แตะ

| ชิ้น | ชะตากรรม |
|---|---|
| `somaos/util/` | ✅ เก็บทั้งดุ้น (rng, hashing, determinism) |
| `somaos/bench/runner.py` `report.py` `gate.py` | ✅ เก็บ — ปรับแกนและ metric |
| `somaos/broker/opt/oracle.py` | ✅ เก็บ — เปลี่ยนเป้าเป็น "จัดสรรคลัง" |
| `somaos/broker/workingset.py` | ✅ เก็บ — ฝั่ง context ไม่เปลี่ยน |
| `somaos/broker/policy.py` | 🔧 ขยาย signature (`store_budget`, `recall_ops`) |
| `somaos/broker/types.py` | 🔧 เพิ่ม addr/vec/level/fidelity/region + zone ใน bundle |
| `somaos/broker/retention.py` | 🔧 แยกเป็น 2 ฟังก์ชัน: จัดลำดับตอนดึง vs เลือกเหยื่อตอนเจือจาง |
| `somaos/bench/metrics.py` | 🔧 0/1 → detail/gist แยกกัน (N-11) |
| `somaos/broker/policies/s_soma.py` | 🔴 รื้อทิ้ง เขียนใหม่ |
| `somaos/bench/trace/generator.py` | 🔴 รื้อ — query หลายระดับ + แรงกดดันด้านความจุ (N-12) |
| `somaos/broker/policies/b2_rag.py` | 🔴 ต้องอยู่ใต้ `store_budget` + เพิ่ม `B2c` (N-14) |

---

## 2. แผน Phase 0b

| WP | งาน | ผลลัพธ์ที่ตรวจได้ | สถานะ |
|---|---|---|---|
| **A0** | `02_INTERFACES.md` ใหม่ | contract นิ่งก่อนเขียนโค้ด | ✅ |
| **A1** | `broker/memory/` — node, address, alias | I1, I5 | ✅ 34 tests |
| **A6** | embedding hash-based + interface สลับได้ (N-13) | I3 | ✅ (อยู่ใน `vector.py`) |
| **A2** | `broker/dilution/` — บันได D0→D4 | I2, I7 | ✅ 24 tests |
| **A3** | ต้นไม้ + แกนความลึก | I6 · depth histogram | ✅ 28 tests |
| **A4** | `broker/regions/` — CORE/TRIGGER + quota | I4 | ✅ 27 tests |
| **A5** | `broker/recall/` — FSM + fast path + WalkPath | I6, I8 | ✅ 28 tests |
| **A7** | consolidation FSM + ตกผลึกนิสัย | นิสัยเกิดจากกิจวัตรจริง ไม่ใช่กองงานสะเปะสะปะ | ✅ 23 tests |
| **A8** | policy `S` ห่อ tree+dilution+recall ใต้ protocol เดิม | สลับผ่าน config ได้ | ⬜ |
| **A9** | trace generator ใหม่ — query 4 ระดับ (N-12) | มี query ที่ทดสอบนิสัย/trigger | ⬜ |
| **A10** | metric detail/gist แยกกัน + `recall_ops` + `store_used` (N-11) | วัดกับ ground truth | ⬜ |
| **A11** | baseline ใต้ `store_budget` เท่ากัน + `B2c` (N-14) | การแข่งยุติธรรมเป็นครั้งแรก | ⬜ |
| **A12** | pre-register KC1–KC5 + แบ่ง seed ชุดใหม่ | ประกาศเกณฑ์ก่อนเห็นผล | ⬜ |
| **A13** | รันเต็มสเกล วัด holdout ครั้งเดียว | เส้นโค้ง M1–M3 | ⬜ |

**ลำดับที่เหลือ:** A7 → A8 → A9 → A10 → A11 → A12 → A13
(A12 ต้องเสร็จ **ก่อน** A13 เสมอ — ประกาศเกณฑ์ก่อนเห็นผล ห้ามสลับ)

### ผลของการรันทั้งระบบ 200 วัน (`somaos/bench/experiments/agent_life.py`)

601 ความทรงจำ · คลัง 96KB (≈ 1/4 ของที่ต้องใช้จริง) · 0.65 วินาที

| | ผล |
|---|---|
| ทุก address ยัง resolve | ✅ ทั้ง 601 |
| คลังเกิน budget หลังจบ cycle | ❌ ไม่เคย |
| นิสัยที่เกิดเอง | `coffee + inbox (21x)` · `river + walk (21x)` — จากกิจวัตร ไม่ใช่จากเรื่องจร |
| วันที่ agent กลับไปนึกถึงบ่อย | ยัง D1_INT8 · fidelity 1.0 · **การเดินแบบเย็น ๆ ยังหาเจอ** |
| วันที่ไม่เคยกลับไปนึกถึง | ตกลง D2_BINARY · ยัง resolve ได้ · ยังชี้หัวข้อตัวเองถูก 0.495 |

**บั๊ก 4 ตัวที่เจอจากการรันนี้** (ทั้งหมดจบลงที่ loop เก็บกวาดที่ไม่มีวันจบ):
address ที่ปลดระวางแล้วกลับมามีชีวิต · node เป็นบรรพบุรุษของตัวเอง ·
D3 เขียนทับเวกเตอร์ของแม่ (ยกเลิกแล้ว ดู `03` §3.5) · การนึกซ้ำไม่ป้องกันการเจือจาง (แก้แล้ว §3.6)

### ผลเบื้องต้นอื่น ๆ (smoke scale, ยังไม่ใช่การวัดจริง)

`python -m somaos.bench.experiments.capacity_curve` — 96 ความทรงจำ ลดคลังจาก 200KB → 200B:

| budget | ใช้จริง | detail | gist |
|---|---|---|---|
| 200,000 | 106,496 | 1.000 | 1.000 |
| 20,000 | 19,904 | 0.938 | 1.000 |
| 3,000 | 2,976 | 0.773 | 1.000 |
| 500 | 480 | 0.517 | 1.000 |
| 200 | 256 | 0.494 | 1.000 |

**รายละเอียดจางลงเรื่อย ๆ แก่นอยู่ครบ ทุก address ยัง resolve ได้ทุกระดับ**
= ลายเซ็นที่ M1 ต้องการ แต่ยังเป็นข้อมูลสังเคราะห์ที่ gist ง่ายเกินไป (มีแค่ 8 กลุ่มที่แยกกันชัด)
**ตัวเลขจริงต้องมาจาก A13 เท่านั้น**

---

## 3. ที่ตัดสินไปแล้วระหว่างทาง (ทบทวนได้ ถ้าไม่เห็นด้วยบอกได้)

1. **N-13 ยืนยัน** — embedding เป็น hash-based deterministic ใน 0b เพราะกำลังทดสอบ *โครงสร้าง*
   ไม่ใช่ *คุณภาพ embedding* · interface สลับเป็นของจริงได้ที่ `vector.embed` จุดเดียว
2. **`dim = 256`** — จากผลวัด §3.3 ของ `03` เป็นจุดที่ binary ยังชี้กลุ่มถูก 1.00
3. **ตัด "ลดมิติ" ออกจากบันไดทั้งขั้น** — sign bit คือ SimHash, บิต = ขนาดตัวอย่างของการประมาณมุม
   ลดมิติเสียความแม่นเร็วกว่าที่ประหยัด (ทั้งทฤษฎีและวัดแล้ว)
4. **`COUNTER_FLOOR = 0.7`** — ต่ำกว่านี้ node จางเกินกว่าจะให้ไปดึง centroid ของแม่
   **เป็นค่า calibration** ต้อง tune บน dev seed เท่านั้น (N-15)
5. **`SKILL` แยกจาก `CORE`** — เพราะ index คนละแบบ: `CORE` resident เสมอ,
   `SKILL` ดึงด้วย cue ของสถานการณ์ (งานฝั่ง habit ชี้ว่านิสัยเป็น context→response
   ไม่ผ่านการคิดถึงเป้าหมาย) ยุบรวมกันจะทำให้ index ผิดชนิด

## 3.1 ที่ยังต้องให้ Nin เคาะ

1. **`CORE` เริ่มต้นมาจากไหน** — เขียนมือตอนสร้าง agent (persona) แล้วให้ตกผลึกเพิ่มเอง?
   (ตอนนี้ `CoreSet.admit()` รองรับทั้งสองทาง ยังไม่ได้เลือก)
2. **branching factor และเกณฑ์แตก/ยุบ node** — ต้องรู้ก่อนทำ A7 (consolidation)
3. **เกณฑ์การตกผลึกนิสัย** — `n_merged ≥ N` เท่าไหร่ และ threshold การกระจายตัวของลูก

---

## 4. กฎที่ต้องรักษาตลอด

- ❌ **ห้ามลบความทรงจำ** (N-01) — `resolve()` ห้ามคืน None
- ❌ **ห้ามให้ engine อ่านข้อความเพื่อตัดสินใจ** (N-02)
- ❌ **ห้ามมี code path ที่ O(N) ต่อการนึกหนึ่งครั้ง** (N-08)
- ❌ ห้าม `CORE`/`TRIGGER` ถูกเจือจางหรือถูกไล่ (N-06)
- ❌ ห้าม tune แล้ววัด holdout ซ้ำ — pre-register ก่อนรัน (N-15)
- ❌ ห้ามใช้ seed holdout ชุดเก่า — ถูกเผาไปแล้ว
- ❌ ห้ามสร้าง `kernel/`, `registry/`, `cortex/`, `modelbus/`, `trace/`, `packs/`
- ⚠️ แก้ decision ที่ล็อกแล้ว (N-01..N-15) ต้องขออนุมัติก่อน
- ⚠️ push ทันทีที่จบแต่ละ WP
