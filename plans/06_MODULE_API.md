# 06_MODULE_API.md — ใช้ SomaOS เป็น module ในแอป

> **สถานะ:** ใช้ได้จริงแล้วสำหรับ Python
> **ทดสอบ:** `tests/test_module_api.py` (33 ข้อ) · รวมทั้งโปรเจกต์ 616 ข้อ
> **ยังไม่มี:** LLM จริง, embedding จริง, การใช้พร้อมกันหลาย thread, C/Rust port

---

## 1. ใช้ยังไง

```python
from somaos.broker import SomaOS, Observation, Cue, Intent, CoreLevel

soma = SomaOS(
    store_budget_bytes=1_000_000,    # "ขนาดสมอง"
    context_budget_tokens=2048,      # วางบนโต๊ะได้เท่าไหร่
    recall_ops_budget=32,            # พยายามนึกได้แค่ไหน
)

soma.seed_identity(("careful", "asks-before-acting"), level=CoreLevel.TRAIT)

soma.remember(Observation.of("nin", "coffee", "morning", tick=day, topic="routine"))
soma.intend(Intent(id="standup", kind="time", due_tick=day + 1))

fired = soma.tick(day)                       # timer + event + จัดระเบียบ
found = soma.recall(Cue.about("incident", tick=day))

soma.save("agent.somaos")
soma = SomaOS.load("agent.somaos")
```

ไม่ต้อง import อะไรจาก `somaos.bench` เลย — และมี test บังคับไว้
(`test_layering.py::test_broker_never_imports_bench`)

---

## 2. หกคำสั่ง

| คำสั่ง | ทำอะไร | คืนอะไร |
|---|---|---|
| `remember(obs)` | เก็บสิ่งที่เกิดขึ้นหนึ่งอย่าง | address ที่**ตอบได้เสมอ** |
| `intend(intent)` | ตั้งเรื่องที่จะทำทีหลัง | trigger id · ถือไว้ฟรี |
| `tick(t, cues=…)` | เดินเวลา + จัดระเบียบ | id ของเรื่องที่ถึงกำหนด |
| `recall(cue)` | พยายามนึก | `Recollection` — ไม่เคย raise |
| `save(path)` | เขียนลงดิสก์แบบ atomic | จำนวน record |
| `SomaOS.load(path)` | อ่านกลับ | `SomaOS` |

`recall()` คืน **keys กับข้อความอ้างอิง ไม่คืนเวกเตอร์** — แอปควรอ่านสิ่งที่ agent จำได้
ไม่ใช่เรขาคณิตที่มันใช้จำ

---

## 3. สองจุดต่อ

### 3.1 embedder — นิยามคำว่า "คล้าย"

```python
from somaos.broker import SomaOS, CallableEmbedder

soma = SomaOS(
    store_budget_bytes=8_000_000,
    embedder=CallableEmbedder(my_model.encode, dim=768),
)
```

broker **ไม่มี client ไม่มี transport ไม่มี SDK ของเจ้าไหน** ผู้เรียกเป็นเจ้าของ connection
เวกเตอร์ที่คืนมาจะถูก normalise ให้เอง (encoder ที่คืนเวกเตอร์ไม่ normalise เป็นเรื่องปกติ
และอาการที่ตามมา — fidelity เกิน 1.0, เจือจางแล้วดูเหมือนดีขึ้น — อยู่ไกลจากต้นเหตุมาก)

> **⚠️ เปลี่ยน embedder = เปลี่ยนเรขาคณิต และค่าปรับแต่งทั้งหมดจูนมาบนเรขาคณิตเดิม**
> `COHERENCE=0.55` (เกณฑ์ตกผลึกนิสัย), `MAX_CHILDREN=12` (เกณฑ์แตกกลุ่ม),
> `STRENGTH_WEIGHT=0.15` (น้ำหนักความคุ้นเทียบกับความคล้าย) ทั้งหมดนี้ตั้งบน hash geometry
> **ต้องจูนใหม่ และห้ามเอาตัวเลขเดิมไปอ้างกับ encoder ใหม่**

> **⚠️ มิติเปลี่ยน = ตัวเลขความจุทั้งหมดเปลี่ยน**
> ตัวเลขใน `05_EMBEDDED_TARGET.md` คิดบน 256 มิติ · 768 มิติ = คูณสาม
> ถามจาก `bytes_per_memory(embedder)` อย่าใช้ตัวเลขจากเอกสาร

### 3.2 navigator — ใครเลือกว่าจะเดินไปไหน

```python
from somaos.broker import SomaOS, CallableNavigator

def choose(view):        # view เป็น dict ที่ json.dumps ได้ ไม่มีเวกเตอร์
    return ask_llm(view) # ต้องคืนหนึ่งใน view["options"] หรือ {"move": "stop"}

soma = SomaOS(store_budget_bytes=1_000_000, navigator=CallableNavigator(choose))
```

`view` ที่ LLM เห็น:

```json
{
  "state": "navigate",
  "here": {"addr": "addr:6603…", "level": 2, "keys": ["topic0"], "span": [0, 22],
           "text_ref": "the topic0 stretch"},
  "ops_left": 15,
  "tokens_left": 216,
  "materialized": 0,
  "options": [
    {"move": "ascend"},
    {"move": "descend", "addr": "addr:5a2f…", "score": 1.139, "level": 1,
     "keys": ["topic0", "person0", "ep27"], "text_ref": "t9: topic0 person0 ep27"},
    {"move": "materialize"},
    {"move": "stop"}
  ]
}
```

**สิ่งที่ LLM เห็นได้มีแค่เพื่อนบ้านของจุดที่ยืนอยู่** ไม่ใช่ทั้งคลัง —
ตัวเลือกที่มองเห็นทั้งคลังไม่ใช่การนำทาง มันคือการสแกน ซึ่งเป็นสิ่งที่ต้นไม้มีไว้เพื่อเลี่ยง
(`reveal_text=False` ปิดข้อความได้ ใช้ตอนตรวจ invariant V1 หรือตอนข้อมูลอ่อนไหว)

**LLM ตอบมั่วได้ ระบบไม่พัง:**

| อาการ | ผล | ตัวนับ |
|---|---|---|
| เลือกท่าที่ไม่มีอยู่ / address ผี | จบการเดินด้วยของที่หาได้แล้ว | `off_menu` |
| เลือกท่าถูกกฎแต่ไม่คืบ (หยิบซ้ำ) | หยุดหลัง 3 ครั้งติด | `stalls` |
| โมเดลล่ม / exception | จบการเดิน ยังตอบได้ | — |

ตั้ง `on_error="raise"` ตอน**ทดลอง** — การวัดว่า "ให้ LLM เลือกเองดีกว่าไหม"
ห้ามกลืนความผิดพลาดของโมเดลเงียบ ๆ ไม่งั้นจะกลายเป็นการวัดการกลืน

`FastPathNavigator` เป็น **default และเป็น control** — ทุกตัวเลขที่รายงานไว้วัดด้วยตัวนี้
มันเป็น searcher จริง ไม่ใช่ stub และ `test_the_fast_path_is_a_real_baseline_not_a_stub`
คุมไว้ว่า chooser แบบมั่ว ๆ ต้องไม่ชนะมัน

---

## 4. ไฟล์ที่เซฟ

JSON Lines มี header ระบุเวอร์ชัน · `.partial` แล้ว rename (crash กลางทางไม่ทำให้ของเดิมหาย)

```
{"kind":"header","format":"somaos-memory","version":1,…}
{"kind":"node","addr":"addr:…","vec":"<base64>","grade":1,…}
{"kind":"alias","old":"addr:…","new":"addr:…","cosine":0.83}
{"kind":"counter",…} {"kind":"core",…} {"kind":"trigger",…}
```

เวกเตอร์เก็บ**ตามเกรดจริง** — D0 float32, D1 int8, D2 อัดบิตละมิติ — ตรงกับที่ `nbytes` คิดเงิน
กลับมาแล้วเหมือนเดิมทุกอย่างรวมถึง dtype

**ไฟล์ใหญ่กว่า store ที่มันเก็บ** เต็มความคม ~2 เท่า · บีบเป็น sign bits ~13 เท่า
ไม่ใช่บั๊ก: base64 กิน 1/3 และพอเวกเตอร์เหลือ 32 ไบต์ **address กลายเป็นส่วนใหญ่ของไฟล์**
(แต่ละ node พก address ตัวเอง 71 ตัวอักษร บวกของพ่อ บวกของลูก)
นี่คือราคาของ format ที่ grep ได้ ซึ่งคุ้มสำหรับ host — และเป็นเหตุผลว่าทำไม
**format ของ MCU ต้องเป็นคนละอัน** (ต้องอ่านทีละ node จาก flash ได้ ไม่ต้อง parse JSON)

`save(keep_text=False)` ตัดข้อความอ้างอิงทิ้งทั้งหมด — รองรับไว้เพราะ invariant V1
บอกว่าตัดข้อความแล้วการค้นต้องไม่เปลี่ยน ซึ่งจะตรวจได้จริงก็ต่อเมื่อเซฟแบบนั้นได้จริง
(และเพราะข้อความคือส่วนเดียวของความทรงจำที่อาจมีข้อมูลส่วนตัว)

---

## 5. ยังไม่มี — พูดตรง ๆ

| ขาด | ผลกระทบ |
|---|---|
| **LLM จริง / embedding จริง** | จุดต่อพร้อม แต่ยังไม่เคยต่อ และค่าปรับแต่งต้องจูนใหม่ |
| **thread safety** | `SomaOS` หนึ่งตัว = หนึ่ง thread · หลาย agent ให้แยก instance |
| **ทางเข้าจากข้อความจริง** | แอปต้องแปลงข้อความเป็น `keys` เอง ยังไม่มีตัวช่วย และ **การเลือก keys เป็นตัวตัดสินว่า agent จะเห็นอะไรเกี่ยวกับอะไร** |
| **migration ของ format** | version 1 ยังไม่มีทางอัปเกรด อ่านไฟล์เวอร์ชันอื่นจะ raise |
| **incremental save** | ทุกครั้งเขียนทั้งไฟล์ · store ใหญ่ ๆ จะช้า |
| **C / Rust port** | ยังลง MCU ไม่ได้ ดู `05_EMBEDDED_TARGET.md` |

---

## 6. ที่ควรทำต่อ

1. **สอบ holdout ก่อนเปลี่ยน embedding** — ทุกอย่างที่วัดมาคือข้อพิสูจน์เรื่อง*โครงสร้าง*
   ถ้าเปลี่ยน encoder ก่อน จะแยกไม่ออกว่าผลมาจากโครงสร้างหรือจาก encoder ที่ดีขึ้น
   (ยังค้างเรื่องเป้าความเร็ว KC3 ที่ต้องตัดสินก่อน)
2. **ต่อ LLM จริงผ่าน `CallableNavigator`** แล้ววัดเทียบ `FastPathNavigator` ตรง ๆ
   นับ `calls` / `stalls` / `off_menu` เป็นต้นทุนและคุณภาพของโมเดล
3. **ต่อ embedding จริง** แล้วจูน `COHERENCE` / `MAX_CHILDREN` / `STRENGTH_WEIGHT` ใหม่
4. **format สำหรับ flash** สำหรับงานฝั่งชิป
