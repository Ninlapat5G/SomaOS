# 04 — พื้นฐานจากงานวิจัยความจำมนุษย์ → การออกแบบ tree

> **จุดประสงค์:** ตอบคำถามว่า *"ในพฤติกรรมมนุษย์ มีอะไรบ้างที่ต้องเก็บเป็นหัวข้อ"*
> ด้วยงานวิจัยที่มีอยู่จริง แทนที่จะเดาเอาเอง แล้ว map ลงเป็นโครงสร้างของ SomaOS
> 2026-08-28 · ประกอบ `plans/03_MEMORY_ARCHITECTURE.md`
>
> **กฎของเอกสารนี้:** ทุกหัวข้อต้องมี (1) งานวิจัยที่อ้างได้ (2) สิ่งที่ SomaOS ทำตาม
> (3) **สิ่งที่จงใจไม่ทำตาม พร้อมเหตุผล** — §9 คือส่วนที่สำคัญที่สุด เพราะสมองไม่ใช่สเปกที่ดีทุกข้อ

---

## 1. ภาพรวม: ทำไมต้องแยกเป็นหลายระบบ ไม่ใช่กองเดียว

**Squire's taxonomy** (Squire & Zola-Morgan) แบ่งความจำระยะยาวเป็นสองสายใหญ่ที่
**พึ่งโครงสร้างสมองคนละส่วนและเสียหายแยกกันได้**:

```
Long-term memory
├── Declarative (บอกเล่าได้ · hippocampus/medial temporal lobe)
│   ├── Episodic   — เหตุการณ์ ณ เวลาและที่ใดที่หนึ่ง
│   └── Semantic   — ข้อเท็จจริงที่หลุดจากบริบทที่เรียนมาแล้ว
└── Non-declarative (บอกเล่าไม่ได้ · basal ganglia, cerebellum, cortex)
    ├── Procedural — ทักษะ นิสัย
    ├── Priming
    └── Conditioning
```

**หลักฐานที่หนักที่สุดคือผู้ป่วย H.M.** — เสีย hippocampus ทั้งสองข้าง จำเหตุการณ์ใหม่ไม่ได้เลย
**แต่ยังเรียนทักษะใหม่ได้** (เก่งขึ้นทุกวันจากการฝึก ทั้งที่ไม่รู้ตัวว่าเคยฝึก)

> **ข้อสรุปสำหรับ SomaOS:** "ความจำ" ไม่ใช่ของอย่างเดียว การเอาทุกอย่างยัดใน vector store
> ก้อนเดียวไม่ใช่แค่ไม่เป็นระเบียบ — **มันขัดกับหลักฐานว่าระบบเหล่านี้แยกกันจริง**
> → รองรับการแบ่งภูมิภาคใน `03_MEMORY_ARCHITECTURE.md` §4 โดยตรง

**Tulving** แยก episodic ออกจาก semantic: episodic ผูกกับ "ตอนนั้น ที่นั่น ฉันรู้สึกยังไง"
ส่วน semantic คือสิ่งที่เหลือหลัง**หลุดจากบริบท**แล้ว
→ ใน SomaOS นี่คือความสัมพันธ์ **ลูก→แม่** ในต้นไม้: semantic คือสิ่งที่ได้จากการยุบ episodic หลาย ๆ อัน

---

## 2. ★ โครงสร้างต้นไม้: Conway's Self-Memory System

**นี่คืองานที่ตอบคำถามของ Nin ตรงที่สุด** — Conway & Pleydell-Pearce (2000) เสนอว่า
**ความจำอัตชีวประวัติถูกจัดเก็บเป็นลำดับชั้น 3 ระดับ** ไม่ใช่กองแบน:

```
Lifetime periods          "ตอนเรียนมหาลัย"  "ตอนทำงานที่แรก"
        │                 (เดือน–ปี · ผูกกับเป้าหมายชีวิตช่วงนั้น)
        ▼
General events            "ช่วงที่ทำโปรเจกต์จบ"  "ตอนไปเที่ยวญี่ปุ่นครั้งนั้น"
        │                 (วัน–สัปดาห์ · เหตุการณ์ซ้ำ ๆ หรือเหตุการณ์ยาว)
        ▼
Event-specific knowledge  "ภาพตอนกดปุ่ม submit ตอนตีสอง"
                          (วินาที–นาที · รายละเอียดทางประสาทสัมผัส)
```

### ข้อค้นพบที่เปลี่ยนดีไซน์ของเรา 2 ข้อ

**(ก) การนึกออกไม่ได้เริ่มจากราก และไม่ได้เริ่มจากใบ — มันเริ่มที่ชั้นกลาง**

> generative retrieval มัก**เริ่มที่ระดับ general event** แล้วค่อยเจาะลงหรือถอยขึ้น

นี่คือเหตุผลว่าทำไมคนถึงนึก *"ช่วงนั้นน่ะ ตอนที่..."* ออกก่อน แล้วรายละเอียดค่อยตามมา
→ **SomaOS: จุดเริ่มของการเดินต้นไม้คือชั้น general event ไม่ใช่ root**
   (ดีไซน์เดิมของผมให้เริ่มที่ root ซึ่งผิดทั้งเชิงหลักฐานและเชิงประสิทธิภาพ — แก้แล้วใน §5 ของ 03)

**(ข) ความจำถูก "ประกอบขึ้น" ตอนนึก ไม่ได้ถูกดึงมาทั้งก้อน**
→ `ContextBundle` ประกอบจากหลายชั้น ไม่ใช่การ copy node เดียวออกมา

---

## 3. ★ "เจือจาง" มีชื่อในงานวิจัย: Fuzzy-Trace Theory

Brainerd & Reyna เสนอว่าทุกเหตุการณ์ถูกเข้ารหัส **ขนานกันสองแบบ**:

| | เก็บอะไร | อยู่นานแค่ไหน |
|---|---|---|
| **Verbatim trace** | รายละเอียดพื้นผิว คำต่อคำ บริบทที่แน่นอน | **สลายเร็ว** (ระดับวัน) |
| **Gist trace** | ความหมาย ใจความ ความสัมพันธ์ | **อยู่นานกว่ามาก** |

หลังผ่านไปไม่กี่วัน verbatim หายไปแต่ gist ยังเข้าถึงได้และยังเอาไปตีความต่อได้

> **นี่คือ "ลืมรายละเอียด แต่พอจำได้" ที่ Nin อธิบาย — มีชื่อเรียก มีหลักฐาน มีคนวัดมาแล้ว**

### สิ่งที่ได้มาโดยตรง 2 อย่าง

1. **บันไดการเจือจางเดินถูกทิศ** — ต้องเสีย verbatim ก่อน แล้ว gist ค่อยจางทีหลัง ไม่ใช่ตกพร้อมกัน
2. **การให้คะแนนต้องแยกสองเส้น** (N-11) — `detail_score` กับ `gist_score`
   **การให้คะแนนแบบ 0/1 ของรอบเก่าคือการวัดแต่ verbatim** จึงมองไม่เห็นสิ่งที่โปรเจกต์นี้ให้ค่าที่สุด

### หลักฐานเสริมจากพยาธิสภาพ

ในภาวะ **semantic dementia** ผู้ป่วยเสียความรู้ระดับเฉพาะเจาะจงก่อนความรู้ระดับหมวดกว้าง —
ตอบ "มันคือสัตว์" ได้ ตอนที่ตอบ "มันคือม้าลาย" ไม่ได้แล้ว
→ **นี่คือรูปแบบการเสื่อมแบบเดียวกับบันได D0→D4 เป๊ะ** ความรู้ระดับ superordinate อยู่รอดท้ายสุด

---

## 4. ★★ สองแกนของการลืม: Bjork's New Theory of Disuse

**นี่คืองานที่ยืนยันดีไซน์สองแกนของเราตรงที่สุด และเป็นสิ่งที่ผมไม่ได้เดาเอาเอง**

Bjork & Bjork (1992) แยกความแข็งแรงของความทรงจำเป็น **สองค่าที่อิสระต่อกัน**:

| | คืออะไร | พฤติกรรม |
|---|---|---|
| **Storage strength** | เรียนมาลึกแค่ไหน | สร้างช้า **แต่แทบไม่เสื่อม** |
| **Retrieval strength** | ตอนนี้ดึงออกมาได้ง่ายแค่ไหน | **ตกเมื่อไม่ได้ใช้** ขึ้นเร็วเมื่อถูกดึง |

> **การลืม = retrieval strength ลด ไม่ใช่การลบ** — ข้อมูลยังอยู่ แค่เข้าถึงยากขึ้น

| Bjork | SomaOS | ตรงกันแค่ไหน |
|---|---|---|
| retrieval strength | **แกนความลึก** (splay: ใช้แล้วตื้น ไม่ใช้แล้วจม) | ตรง 1:1 |
| storage strength | **แกนความคมชัด** (`fidelity`) | ตรงบางส่วน — ดู §9 ข้อ 1 |
| การดึงกลับทำให้เข้าถึงง่ายขึ้น | promote-on-access | ตรง |
| ไม่มีอะไรถูกลบ | N-01 `resolve()` ห้ามคืน None | ตรง |

**ข้อที่แรงที่สุด:** ทฤษฎีนี้บอกว่าสมองพัฒนากลไกนี้ขึ้นมา **เพื่อจัดการข้อมูลปริมาณมหาศาล
โดยทำให้ของที่ไม่ได้ใช้เข้าถึงยากขึ้น** — ซึ่งคือปัญหาเดียวกับที่ SomaOS แก้ทุกประการ

---

## 5. ★ TRIGGER: Prospective Memory มีสองชนิด และสองกลไก

Einstein & McDaniel แยก prospective memory เป็น:

| ชนิด | ตัวอย่าง | SomaOS |
|---|---|---|
| **Event-based** | "เจอหมอเมื่อไหร่ ให้ถามเรื่องยา" | `TRIGGER.event` — hash bucket ตาม event key |
| **Time-based** | "โทรหาแม่ตอนหกโมง" | `TRIGGER.time` — timer wheel |

และ **Multiprocess theory** บอกว่าการนึกได้เกิดจาก**สองกลไกที่ต้นทุนต่างกันมาก**:

| กลไก | เกิดยังไง | ต้นทุน | SomaOS |
|---|---|---|---|
| **Spontaneous retrieval** | สิ่งเร้าตรงกับ cue → ผุดขึ้นเอง | ~ฟรี | lookup O(1) ตอน perceive |
| **Monitoring** | ต้องคอยเฝ้าระวังเอง | **แพง แย่ง attention จากงานหลัก** | เดินตรวจ predicate ทุก tick |

> **ผลต่อดีไซน์ที่ชัดเจน:** trigger ที่ผูกกับ cue ตรง ๆ ต้องออกแบบให้เป็น **hash O(1)**
> ส่วน trigger แบบมีเงื่อนไข (`ถ้า mood < k`) ต้อง **จ่าย `recall_ops` จริง** เพราะมันแพงจริงในสมองด้วย
> → ต้นทุนใน cost model สะท้อนต้นทุนทางปัญญาจริง ไม่ใช่ตัวเลขที่ตั้งขึ้นลอย ๆ

**และงานวิจัยยังพบว่า intention ที่ "ค้างอยู่" (suspended) ยังผุดขึ้นเองได้ ส่วนที่ "จบแล้ว" ไม่ผุด**
→ FSM ของ trigger ต้องมีสถานะ `SUSPENDED` แยกจาก `RETIRED` (ไม่ใช่แค่ armed/fired)

---

## 6. ★ CORE ไม่ใช่ก้อนเดียว: McAdams' Three Levels

ถ้าจะเก็บ "นิสัย" ต้องรู้ก่อนว่านิสัยของคนมีกี่ชั้น McAdams เสนอ 3 ชั้นที่ **เปลี่ยนด้วยอัตราต่างกัน**:

| ชั้น | คืออะไร | เปลี่ยนเร็วแค่ไหน | `CORE` sub-level |
|---|---|---|---|
| **1. Dispositional traits** | ลักษณะกว้าง ๆ (แนว Big Five) — "เป็นคนระวังตัว" | ช้ามาก (ปี) | `CORE.trait` |
| **2. Characteristic adaptations** | เป้าหมาย ค่านิยม วิธีรับมือ ผูกกับบริบท/บทบาท | กลาง (เดือน) | `CORE.adaptation` |
| **3. Narrative identity** | เรื่องเล่าที่ร้อยทุกอย่างเป็นตัวตนที่ต่อเนื่อง | ช้า แต่ถูกเขียนใหม่ได้ | `CORE.narrative` |

### 🔗 จุดเชื่อมที่สำคัญที่สุดในเอกสารนี้

Conway บอกว่า **life story schema เป็นตัว index ให้ lifetime periods**
McAdams บอกว่า **narrative identity คือชั้นบนสุดของบุคลิกภาพ**

> **สองอันนี้คืออันเดียวกัน** → ใน SomaOS: **`CORE.narrative` คือ root ของต้นไม้ `ARCHIVE`**

แปลว่า `CORE` กับ `ARCHIVE` ไม่ใช่สองคลังที่แยกขาด — **`CORE` คือยอดของต้นไม้เดียวกัน**
และนี่อธิบายกลไกจริงว่าทำไม "ตัวตนมีผลต่อสิ่งที่นึกออก": ตัวตนคือจุดตั้งต้นของการเดินทุกครั้ง

---

## 7. SKILL/นิสัย: keyed ด้วยสถานการณ์ ไม่ใช่หัวข้อ

งานวิจัยเรื่อง habit (สาย Wood & Neal) นิยามนิสัยเป็น **ความเชื่อมโยง context→response**
ที่ทำงาน**โดยไม่ผ่านการคิดถึงเป้าหมาย** — เจอบริบทเดิม พฤติกรรมเดิมก็มาเอง
และงานฝั่ง neuroscience (Graybiel) พบว่าลำดับการกระทำถูก "chunk" เป็นหน่วยเดียว

> **ผลต่อดีไซน์:** `SKILL` **ห้าม index ด้วยความคล้ายของหัวข้อ** — ต้อง index ด้วย **cue ของสถานการณ์**
> นี่คือเหตุผลที่ `SKILL` ต้องเป็นภูมิภาคแยก ไม่ใช่แค่ node ชนิดหนึ่งใน `ARCHIVE`
> (การค้นแบบ semantic similarity ตอบคำถาม "เรื่องนี้เกี่ยวกับอะไร" — ไม่ใช่ "ตอนนี้ควรทำอะไร")

**และ chunking บอกว่า `SKILL` node หนึ่งควรเป็นลำดับการกระทำทั้งชุด ไม่ใช่ก้าวเดียว**

---

## 8. ทำไมต้อง consolidation แบบ batch: Complementary Learning Systems

McClelland, McNaughton & O'Reilly (1995) อธิบายว่าทำไมสมองถึงต้องมีสองระบบ:

- **Hippocampus** — เรียนเร็ว จำเหตุการณ์เดี่ยวได้ทันที แต่ความจุจำกัด
- **Neocortex** — เรียนช้า สกัดโครงสร้างที่ใช้ร่วมกันได้จากหลายเหตุการณ์ ความจุมหาศาล
- **ถ้าให้ neocortex เรียนเร็ว → catastrophic interference** ของใหม่ทับของเก่าพัง
- ทางแก้ของสมอง: **replay แบบสลับ (interleaved) ตอนหลับ** ค่อย ๆ ย้ายความรู้ข้ามระบบ

> **นี่คือเหตุผลเชิงกลไกว่าทำไม consolidation ต้องเป็น slow path แบบ batch — ไม่ใช่แค่ "ประหยัดค่า LLM"**
> ถ้ายุบ episodic เป็น semantic แบบทันทีทุกครั้งที่มี observation ใหม่ โครงสร้างที่สกัดได้จะสั่นและทับของเดิม
> → รองรับ two-speed architecture (`03` §5.4) ด้วยเหตุผลที่แข็งกว่าเรื่องต้นทุน

---

## 9. ⚠️ สิ่งที่จงใจ **ไม่** ลอกจากสมอง (สำคัญที่สุดในเอกสารนี้)

| # | สมองทำแบบนี้ | SomaOS ทำต่าง | เหตุผล |
|---|---|---|---|
| 1 | **storage strength แทบไม่เสื่อม** — ความจุใหญ่จนไม่ต้องบีบ | `fidelity` **เสื่อมได้** เมื่อ `store_budget` เต็ม | เรามีขีดจำกัดเป็นไบต์จริง ๆ นี่คือจุดที่เราจงใจต่างจาก Bjork และต้องพูดให้ชัดเวลารายงานผล |
| 2 | **recall เป็นการประกอบใหม่ และแต่งเติมได้** (Bartlett; false memory ในสาย FTT เอง) | ห้ามสังเคราะห์รายละเอียดที่ไม่มีจริง — เก็บ `raw_refs` เสมอ | ระบบที่มั่นใจในสิ่งที่มันแต่งเองคือระบบที่ใช้งานไม่ได้ |
| 3 | ลืมแล้วไม่รู้ว่าลืมอะไร | **log ทุกครั้งที่เจือจาง** ว่าอะไรจางเพราะอะไร | ต้อง audit ได้ |
| 4 | ยิ่งอารมณ์แรง ยิ่งจำแม่น (amygdala modulation) | surprise/arousal เป็นแค่**สัญญาณจัดชั้น** ไม่ใช่ประตูตัดสินเก็บ/ทิ้ง | รอบที่แล้วทำแบบสมองแล้วพัง (`ARCHIVE_PHASE0_RESULT.md`) — เดาผิดต้องแค่เข้าถึงช้าลง ไม่ใช่ข้อมูลหาย |
| 5 | ไม่มี guarantee เชิง complexity | guarantee มาจาก OS/DB/IR theory | metaphor ใช้หาไอเดีย ไม่ใช้ค้ำประกัน |
| 6 | **ในที่สุดแก่นก็หายได้** — วรรณกรรม semantic dementia ระบุว่า superordinate knowledge ก็เสื่อมตามมาเมื่อโรคดำเนินไป | **พื้น D4 ของเราไม่จางต่ออีกเลย** — "เคยมีเรื่องแบบนี้ n ครั้ง" อยู่ตลอดกาล | เจอตอนตรวจเอกสารรอบสอง · เป็นการเลือกโดยตั้งใจตาม N-01 ไม่ใช่การลอกสมอง และต้องรายงานว่าเป็นจุดที่ต่าง |

---

## 10. สรุปเป็นตารางออกแบบ: อะไรต้องเก็บเป็นหัวข้อบ้าง

| หัวข้อ | ที่มาจากงานวิจัย | region | ระดับในต้นไม้ | เจือจางถึง |
|---|---|---|---|---|
| ลักษณะนิสัยกว้าง ๆ | McAdams L1 | `CORE.trait` | root-adjacent | ❌ |
| เป้าหมาย · ค่านิยม · วิธีรับมือ | McAdams L2 | `CORE.adaptation` | root-adjacent | ❌ |
| เรื่องเล่าตัวตน | McAdams L3 + Conway life story schema | `CORE.narrative` | **root ของ ARCHIVE** | ❌ |
| ความตั้งใจตามเหตุการณ์ | Einstein & McDaniel (event-based) | `TRIGGER.event` | index O(1) | ❌ |
| ความตั้งใจตามเวลา | Einstein & McDaniel (time-based) | `TRIGGER.time` | timer wheel | ❌ |
| เงื่อนไขที่ต้องเฝ้า | Multiprocess (monitoring) | `TRIGGER.predicate` | เดินตรวจ จ่าย ops | ❌ |
| ทักษะ · ลำดับการกระทำ | Squire non-declarative; Graybiel chunking | `SKILL` | index ด้วย cue สถานการณ์ | D2 |
| ช่วงชีวิต | Conway lifetime periods | `ARCHIVE` L3 | ชั้นบน | D3 |
| เหตุการณ์รวม (จุดเริ่มการนึก ★) | Conway general events | `ARCHIVE` L2 | **ชั้นกลาง = จุดเข้า** | D3 |
| เหตุการณ์เฉพาะ | Conway ESK; Tulving episodic | `ARCHIVE` L1 | ชั้นล่าง | D4 |
| รายละเอียดพื้นผิว | FTT verbatim | `ARCHIVE` L0 (ใบ) | ใบ | D4 |
| ข้อเท็จจริงที่หลุดบริบทแล้ว | Tulving semantic | `ARCHIVE` โหนดที่ยุบแล้ว | ชั้นบน | D3 |

---

## 11. รายการอ้างอิง — **ตรวจสอบครบทุกรายการแล้ว** ✅

> ทุกรายการในเอกสารนี้ถูกเปิดตรวจจากแหล่งออนไลน์แล้ว ไม่มีรายการที่อ้างจากความจำอย่างเดียวเหลืออยู่
> ตรวจรอบสอง (รายการ 7–13) เมื่อ 2026-08-28

### โครงสร้างต้นไม้ · การนึก
1. **Conway, M. A., & Pleydell-Pearce, C. W. (2000).** *The construction of autobiographical memories in the self-memory system.* Psychological Review — ลำดับชั้น lifetime period → general event → ESK และ generative retrieval ที่เริ่มที่ระดับ general event
   [Semantic Scholar](https://www.semanticscholar.org/paper/The-construction-of-autobiographical-memories-in-Conway-Pleydell-Pearce/13241a844c714549c173e239714ae020386172e3) · [PDF](https://www.researchgate.net/profile/Martin-Conway-2/publication/12528554_The_Construction_of_Autobiographical_Memories_in_the_Self-Memory_System/links/0deec51babda329123000000/The-Construction-of-Autobiographical-Memories-in-the-Self-Memory-System.pdf)
2. **Collins, A. M., & Loftus, E. F. (1975).** *A spreading-activation theory of semantic processing.* Psychological Review — พื้นฐานของ move `lateral`: activation แผ่ไปตาม link สองทิศทางระหว่าง node ที่เกี่ยวข้อง
   [PDF ต้นฉบับ](https://faculty.sites.uci.edu/eloftus/files/2024/08/CollinsLoftus_PsychReview_75.pdf) · [Semantic Scholar](https://www.semanticscholar.org/paper/A-spreading-activation-theory-of-semantic-Collins-Loftus/61374d14a581b03af7e4fe0342a722ea94911490)

### สองแกนของการลืม · การเจือจาง
3. **Bjork, R. A., & Bjork, E. L. (1992).** *A new theory of disuse and an old theory of stimulus fluctuation* — storage strength vs retrieval strength; "ลืม = เข้าถึงไม่ได้ ไม่ใช่ถูกลบ"
   [ResearchGate](https://www.researchgate.net/publication/281322665_A_new_theory_of_disuse_and_an_old_theory_of_stimulus_fluctuation) · [Bjork Lab](https://bjorklab.psych.ucla.edu/research/)
4. **Brainerd, C. J., & Reyna, V. F.** — Fuzzy-trace theory: verbatim vs gist และ differential survival rates
   [PubMed](https://pubmed.ncbi.nlm.nih.gov/11605365/) · [ScienceDirect](https://www.sciencedirect.com/topics/neuroscience/fuzzy-trace-theory)
5. **Warrington (1975); Hodges et al. (1992); Rogers et al.** — semantic dementia: **superordinate advantage** ผู้ป่วยระบุด้วยคำระดับหมวด ("สัตว์") ได้แม่นกว่าชื่อเฉพาะ ("ม้าลาย") อย่างมีนัยสำคัญ
   [ScienceDirect overview](https://www.sciencedirect.com/topics/pharmacology-toxicology-and-pharmaceutical-science/semantic-dementia) · [Contrasting patterns of comprehension, PubMed](https://www.unboundmedicine.com/medline/citation/19086203/) · [Lost in semantic space, Brain](https://academic.oup.com/brain/article/129/5/1152/327149)
   > ⚠️ **ข้อที่เจอตอนตรวจและต้องบันทึก:** วรรณกรรมระบุว่า *"over time, superordinate knowledge may also succumb"* —
   > **ในคนแก่นก็หายได้ในที่สุด** ส่วนพื้น D4 ของเราไม่จางต่ออีกเลย → ดู §9 ข้อ 6
6. **Bartlett, F. C. (1932).** *Remembering* — War of the Ghosts: การจำเป็นการประกอบใหม่ตาม schema
   [ทบทวนใน Memory (2022)](https://www.tandfonline.com/doi/full/10.1080/09658211.2022.2059514) · [สรุปการศึกษา](https://www.psychstory.co.uk/memory/reconstructive-memory-and-schemas)
   > กลไกที่ Bartlett แยกไว้ 3 อย่าง map ลง SomaOS ได้ตรง ๆ:
   > **levelling** (ตัดรายละเอียดที่ไม่สำคัญออก เรื่องสั้นลงเรื่อย ๆ) ≈ บันไดการเจือจางของเรา
   > **assimilation** และ **sharpening** (แต่งให้เข้ากับ schema เดิม · ยัดเหตุผลที่ไม่มีในต้นฉบับ) = สิ่งที่เรา**ห้าม**โดยเด็ดขาด (§9 ข้อ 2)

### การแยกระบบความจำ
7. **Squire, L. R., & Zola-Morgan** — taxonomy declarative/non-declarative; **Scoville & Milner (1957)** ผู้ป่วย H.M.
   ทักษะที่ยังเรียนได้ทั้งที่จำเหตุการณ์ใหม่ไม่ได้: motor skill, mirror-reading (เรียนได้เท่ากลุ่มควบคุม จำได้อย่างน้อย 3 เดือน)
   [The Cognitive Neuroscience of Human Memory Since H.M., PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3192650/) · [Structure and function of declarative and nondeclarative memory systems, PNAS](https://www.pnas.org/doi/10.1073/pnas.93.24.13515) · [Preserved learning in amnesia, Science](https://www.science.org/doi/10.1126/science.7414331)
8. **Tulving, E. (1972).** episodic vs semantic — episodic = เหตุการณ์ที่ผูกกับเวลา/สถานที่/ตัวตน · semantic = ความรู้ที่หลุดบริบทแล้ว
   [Historical perspective, ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0028393220300373) · [Rethinking the distinction (2022)](https://link.springer.com/article/10.3758/s13421-022-01299-x)

### นิสัยและทักษะ
9. **Wood, W., & Neal, D. T. (2007).** *A New Look at Habits and the Habit–Goal Interface.* Psychological Review —
   "perception of contexts triggers the associated response **without a mediating goal**" และ habit ทำนายพฤติกรรมได้แม้คุมตัวแปรเป้าหมายไว้แล้ว
   [PDF ต้นฉบับ, USC](https://dornsife.usc.edu/wendy-wood/wp-content/uploads/sites/183/2023/10/wood.neal_.2007psychrev_a_new_look_at_habits_and_the_interface_between_habits_and_goals.pdf) · [Wood, Mazar & Neal (2022)](https://journals.sagepub.com/doi/abs/10.1177/1745691621994226)
10. **Graybiel, A. M. (1998).** *The basal ganglia and chunking of action repertoires* — ลำดับการกระทำถูก recode เป็น **หน่วยเดียว**; หนูที่วิ่งเขาวงกตซ้ำ ๆ neuron จาก firing ตลอดทาง เหลือ burst ที่ต้นทางกับปลายทาง
    [PubMed](https://pubmed.ncbi.nlm.nih.gov/9753592/) · [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1074742798938436) · [Graybiel (2008) review](https://web.math.princeton.edu/~sswang/basal-ganglia/graybiel08_annu_rev_neurosci_BG-evaluative-brain.pdf)

### ความตั้งใจ · ตัวตน · การนอน
11. **Einstein, G. O., & McDaniel, M. A. (2005).** prospective memory — event/time-based; monitoring vs spontaneous retrieval; **suspended intentions ยังผุดเอง ส่วนที่ finished ไม่ผุด**
    [SAGE](https://journals.sagepub.com/doi/10.1111/j.0963-7214.2005.00382.x) · [PubMed](https://pubmed.ncbi.nlm.nih.gov/16131267/) · [suspended vs finished](https://link.springer.com/article/10.3758/MC.37.4.425)
12. **McAdams, D. P.** — three levels: dispositional traits · characteristic adaptations · narrative identity
    [Traits and Stories, PDF](https://languageandcognition.umd.edu/McAdamsEtAl2004.pdf) · [The Psychology of Life Stories (2001)](https://journals.sagepub.com/doi/10.1037/1089-2680.5.2.100)
13. **McClelland, McNaughton & O'Reilly (1995).** *Why there are complementary learning systems...* Psychological Review 102(3), 419–457 —
    hippocampus เรียนเร็วแบบ pattern-separated · neocortex เรียนช้าเพื่อสกัดโครงสร้าง · **interleaved learning กัน catastrophic interference**
    [PubMed](https://pubmed.ncbi.nlm.nih.gov/7624455/) · [PDF](https://www.researchgate.net/profile/James-Mcclelland-4/publication/15575602_Why_There_are_Complementary_Learning_Systems_in_the_Hippocampus_and_Neocortex/links/0046353bc1872a47b5000000/Why-There-Are-Complementary-Learning-Systems-in-the-Hippocampus-and-Neocortex.pdf)

### เชิงวิศวกรรม (ไม่ใช่งานวิจัยสมอง)
14. **Charikar, M. (2002).** SimHash / random hyperplane rounding — `P[บิตตรงกัน] = 1 − θ/π` พื้นฐานของขั้น D2
    [binary quantization ของ contrastive embeddings (2026)](https://arxiv.org/html/2605.17524)

---

## 12. ⚖️ อะไรมาจากไหน — ตารางที่ต้องไม่โกหกตัวเอง

> **กฎ:** เวลาเขียน paper หรือคุยกับคนนอก ต้องแยกสามกลุ่มนี้ให้ชัด
> การเคลมรวม ๆ ว่า "inspired by human memory" คือสิ่งที่งาน AI memory ส่วนใหญ่ทำ และเป็นสิ่งที่เราจะไม่ทำ

| ส่วนของดีไซน์ | ที่มา | สถานะ |
|---|---|---|
| แยก 4 ภูมิภาคตามหน้าที่ | Squire · H.M. | 🟢 งานวิจัย |
| ต้นไม้ 5 ชั้นของ ARCHIVE | Conway & Pleydell-Pearce | 🟢 งานวิจัย |
| การเดินเริ่มที่ชั้น general event | Conway | 🟢 งานวิจัย |
| move `lateral` | Collins & Loftus | 🟢 งานวิจัย |
| แกนความลึก / แกนความคมชัด แยกกัน | Bjork & Bjork | 🟢 งานวิจัย |
| verbatim ตายก่อน gist | Fuzzy-trace theory | 🟢 งานวิจัย |
| แก่นระดับหมวดอยู่รอดกว่าชื่อเฉพาะ | semantic dementia | 🟢 งานวิจัย |
| `SKILL` เรียกด้วย cue ไม่ผ่านการค้น | Wood & Neal · H.M. | 🟢 งานวิจัย |
| `SKILL` node = ลำดับการกระทำทั้งชุด | Graybiel chunking | 🟢 งานวิจัย |
| trigger event/time + ต้นทุน monitoring ต่างกัน | Einstein & McDaniel | 🟢 งานวิจัย |
| สถานะ `SUSPENDED` แยกจาก `RETIRED` | Einstein & McDaniel | 🟢 งานวิจัย |
| `CORE` 3 ชั้น | McAdams | 🟢 งานวิจัย |
| consolidation เป็น batch + interleaved | CLS (1995) | 🟢 งานวิจัย |
| **`CORE.narrative` = รากของ `ARCHIVE`** | Conway + McAdams | 🟡 **สังเคราะห์เอง** — วรรณกรรมเรียกสองอันนี้ว่า *complementary frameworks* และมีงานที่ใช้คู่กัน **แต่ไม่มีใครบอกว่าเป็นอันเดียวกัน** การรวมเป็นรากเดียวเป็นก้าวเชิงวิศวกรรมของเรา |
| เก็บเป็นเวกเตอร์ · dim=256 | ML/IR | 🔵 วิศวกรรม |
| content address · Merkle · alias table | OS/blockchain | 🔵 วิศวกรรม |
| ขั้น D2 = sign bits | Charikar (CS) + **ผลวัดของเราเอง** | 🔵 วิศวกรรม |
| best-first · beam · การแตก node | algorithm | 🔵 วิศวกรรม |
| ค่าคงที่ทั้ง 12 ตัว | **ตั้งเอง ยังไม่ได้ tune** | 🔵 วิศวกรรม |
| `store_budget_bytes` เป็นเพดานไบต์แข็ง | — | ⚫ **จงใจต่างจากสมอง** (§9) |
| ห้ามสังเคราะห์รายละเอียดที่ไม่มีจริง | — | ⚫ **จงใจต่างจากสมอง** (§9) |

### ⚠️ ข้อจำกัดที่ใหญ่ที่สุด และต้องพูดทุกครั้ง

**งานวิจัยเป็นแรงบันดาลใจให้โครงสร้าง — ไม่ได้ยืนยันว่า implementation ถูก**
ผลวัดทั้งหมดบอกว่า *ของที่สร้างมันทำงาน* ไม่ได้บอกว่า *มันเหมือนความจำคน*
เพราะ **ยังไม่เคยเทียบกับข้อมูลพฤติกรรมมนุษย์จริงเลยสักครั้ง**

ถ้าจะเคลมข้อหลังได้ ต้องทำอย่างน้อยหนึ่งอย่าง: จำลอง forgetting curve ของ Ebbinghaus ให้ตรง ·
สร้าง superordinate advantage แบบเดียวกับผู้ป่วย semantic dementia · หรือทำนายผลการทดลอง
prospective memory ของคนได้

---

## 13. ✅ หลักฐานชิ้นแรกที่เชื่อมกับข้อมูลมนุษย์: superordinate advantage

> `python -m somaos.bench.experiments.superordinate` · เทสต์คุมที่ `tests/test_superordinate_advantage.py`

บีบคลังลงเรื่อย ๆ (แทนการดำเนินของโรค) แล้วถามภาพเดิมสามแบบ **แบบเลือกตอบ 6 ตัวเลือกทุกระดับ**
(เดาสุ่ม = 0.167 เท่ากันทั้งสามระดับ — เหมือนวิธีที่ใช้ทดสอบผู้ป่วยจริง)

| คลัง | เฉพาะเจาะจง<br><small>"ตัวไหน"</small> | ระดับกลาง<br><small>"ของชนิดไหน"</small> | หมวดกว้าง<br><small>"ตระกูลไหน"</small> |
|---|---|---|---|
| 12,000 | 1.000 | 1.000 | 1.000 |
| 5,000 | 0.65 | **0.81** | 1.000 |
| 2,000 | 0.39 | **0.58** | 1.000 |
| 800 | 0.24 | **0.43** | 1.000 |

**ผ่านทั้ง 5 seed ตัวเลขนิ่งมาก** (เฉพาะเจาะจงที่ 800 bytes: 0.22–0.26 ทุก seed)

### ทำไมผลนี้ถึงมีความหมาย ไม่ใช่การวัดตัวเอง

**ส่วนที่วนกลับหาตัวเอง (ยอมรับ):** บันไดการเจือจางถูกออกแบบให้ sign bit เก็บหมวดไว้และทิ้งตัวตน
ดังนั้น *"หมวดกว้างอยู่รอดกว่าเฉพาะเจาะจง"* เป็นสิ่งที่เราใส่เข้าไปเอง — **ข้อนี้อย่างเดียวไม่พิสูจน์อะไร**

**ส่วนที่ไม่ได้ออกแบบใส่ไป:** ระดับกลาง

บันไดไม่รู้จักคำว่า "สามระดับ" มันรู้จักแค่เวกเตอร์กับจำนวนบิตที่เหลือ และต้นไม้ตอนเริ่มมีแค่
**สองชั้น** (แม่ระดับหมวด + ตัวอย่าง) — **ไม่มีชั้นกลางเลย**

ชั้นกลางถูกสร้างโดย `REBALANCE` ซึ่งแตก node ที่กว้างเกิน beam แล้วจัดกลุ่มลูกตามความคล้าย
**ไม่มีใครบอกมันว่าหมาจบตรงไหนแมวเริ่มตรงไหน** การที่กลุ่มที่โผล่มาเองเหล่านั้นรองรับคำตอบ
ระดับกลางที่เสื่อมด้วยอัตราตรงกลางพอดี — **อันนี้เป็นสมบัติของการเสื่อม ไม่ใช่สิ่งที่เขียนลงไป**

**หลักฐานตรง:** รอบแรกผมลืมรัน consolidation ต้นไม้เลยมีแค่สองชั้น ผลออกมา
**ระดับกลางเสื่อมทับเฉพาะเจาะจงเป๊ะ** (0.727 vs 0.769) = หน้าผาสองระดับ ไม่ใช่สามระดับแบบคน
พอให้ต้นไม้สร้างชั้นกลางเอง สามระดับก็แยกออกจากกันทันที

### ⚠️ ขอบเขตของผลนี้

- **taxonomy สังเคราะห์** — โครงสร้าง 3 ชั้นในข้อมูลผมใส่เอง สิ่งที่ emergent คือ*ชั้นกลางของต้นไม้*
  ที่ไปตรงกับมัน ไม่ใช่ตัวโครงสร้างเอง · ถ้าข้อมูลมี 4 ชั้น ต้นไม้จะสร้าง 4 ชั้นไหม **ยังไม่รู้**
- เป็นการตรงกัน**เชิงคุณภาพ** (ลำดับ + ความเป็นขั้น) **ไม่ใช่การ fit กับข้อมูลผู้ป่วยเชิงปริมาณ**
- ยังไม่ได้เทียบกับ dataset ผู้ป่วยจริงแม้แต่ชุดเดียว

**แต่มันเปลี่ยนสถานะของโปรเจกต์จาก "ได้แรงบันดาลใจจากงานวิจัย" เป็น "สร้างรูปแบบที่งานวิจัยบันทึกไว้ได้"**
ซึ่งเป็นคนละชั้นของข้ออ้างกัน
