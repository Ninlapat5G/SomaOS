# plan.md — แผนงานและสถานะ

> อัปเดตล่าสุด: 2026-08-28 — **เริ่ม Phase 0b (ออกแบบใหม่ทั้งหมด)**
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

### 1.1 เอกสาร (รอบนี้)
- [x] `plans/ARCHIVE_PHASE0_RESULT.md` — เก็บผลการวัดของรอบเก่าไว้เป็นหลักฐาน
- [x] `target_SomaOS.md` **v2** — เขียนทับทั้งฉบับ (thesis, moat, memory model, kill criteria ใหม่)
- [x] `plans/03_MEMORY_ARCHITECTURE.md` — สเปกโครงสร้างหน่วยความจำเต็ม
- [x] `plans/01_DECISIONS.md` — N-01..N-15 แทนที่ D-01..D-14 ทั้งชุด
- [x] `CLAUDE.md` — อัปเดตสถานะและกฎ
- [ ] `plans/02_INTERFACES.md` — ยังเป็นของเก่า **ต้องเขียนใหม่ก่อนเริ่มโค้ด**
- [ ] `plans/00_PHASE0_MASTER_PLAN.md` — ยังเป็นของเก่า ต้องเขียนใหม่เป็น master plan ของ 0b

### 1.2 โค้ด — ยังไม่แตะ (ตั้งใจ)
รอเคาะคำถามใน §3 ก่อน

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

| WP | งาน | ผลลัพธ์ที่ตรวจได้ |
|---|---|---|
| **WP-A0** | เขียน `02_INTERFACES.md` + master plan ของ 0b ใหม่ | contract นิ่งก่อนเขียนโค้ด |
| **WP-A1** | `broker/memory/` — node, content address, alias table, ต้นไม้ | I1 (`resolve` ไม่เคยคืน None), I5 |
| **WP-A2** | `broker/dilution/` — บันได D0→D4, บังคับ `store_budget` | I2, I7 (`fidelity` ลดทางเดียว) |
| **WP-A3** | splay promote/sink — แกนความลึก | ของที่ใช้บ่อยตื้นขึ้นจริง วัดเป็น depth histogram |
| **WP-A4** | `broker/regions/` — CORE / TRIGGER / SKILL / ARCHIVE + hard quota | I4 (CORE/TRIGGER ไม่เคยเจือจาง) |
| **WP-A5** | `broker/recall/` — FSM + fast path + `WalkPath` | I6 (ไม่มี O(N)), I8 (มีเส้นทางเสมอ) |
| **WP-A6** | embedding แบบ hash-based deterministic + interface ให้สลับได้ (N-13) | I3 (ลบข้อความแล้วเดินเหมือนเดิม) |
| **WP-A7** | trace generator ใหม่ — query 4 ระดับ (N-12) | มี query ที่ทดสอบนิสัย/trigger จริง |
| **WP-A8** | metrics ใหม่ — detail/gist แยกกัน + `recall_ops` + `store_used` | N-11 |
| **WP-A9** | baseline ใต้ `store_budget` เท่ากัน + `B2c` (N-14) | การแข่งยุติธรรมเป็นครั้งแรก |
| **WP-A10** | pre-register KC1–KC5 + แบ่ง seed ชุดใหม่ | ประกาศเกณฑ์ก่อนเห็นผล |
| **WP-A11** | รันเต็มสเกล วัด holdout ครั้งเดียว | เส้นโค้ง M1–M3 |

**ลำดับบังคับ:** A0 → (A1, A6 คู่กัน) → A2 → A3 → A4 → A5 → (A7, A8, A9) → A10 → A11

---

## 3. คำถามที่ต้องเคาะก่อนเริ่ม WP-A1

1. **embedding แบบ hash-based ของ Phase 0b** — ยืนยัน N-13 ไหม (ผมแนะนำยืนยัน:
   ทดสอบโครงสร้าง ไม่ใช่คุณภาพ embedding แต่ interface สลับได้)
2. **`dim` และค่าบันได quantization** — เริ่มที่ `dim=256`, `D1=int8`, `D3` ลดเหลือ 64 มิติ?
3. **branching factor `b`** ของต้นไม้ และเกณฑ์แตก/ยุบ node
4. **`CORE` เริ่มต้นมาจากไหน** — เขียนมือตอนสร้าง agent (persona) แล้วให้ตกผลึกเพิ่มเอง?
5. **`SKILL` แยกจาก `CORE` จริงไหม** หรือยุบเป็นภูมิภาคเดียวไปก่อนใน 0b

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
