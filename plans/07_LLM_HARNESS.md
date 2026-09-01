# 07_LLM_HARNESS.md — พร้อมต่อ LLM แล้ว

> **สถานะ:** ต่อกับโมเดลจริงแล้ว — **Typhoon** (`typhoon-v2.5-30b-a3b-instruct` ผ่าน
> `plans/08_TYPHOON.md`) การต่อจริงเจอบั๊ก 5 ตัวที่การทดสอบด้วยตัวแทนสคริปต์มองไม่เห็นเลย
> (ดู §3.5 และ §5.5) แก้แล้วทั้งหมด **719 tests ผ่าน** — **แต่ตัวเลข detail/gist ล่าสุดยังไม่ผ่านรอบวัด
> เดียวหลังแก้ครบ §5.5 บอกไว้ชัดว่าอย่าอ่านเป็นผลสุดท้าย**
> **ทุกอย่างยังรันได้แบบไม่ต้องมี endpoint** ผ่าน `--replay`

---

## 1. พอ endpoint พร้อม รันคำสั่งเดียว

```bash
# ทดลองจริง
python -m somaos.bench.experiments.agent_directed \
    --endpoint http://localhost:11434 \
    --model gemma3:4b \
    --record runs/walk.jsonl

# รันซ้ำจาก transcript ที่บันทึกไว้ ไม่ต้องมี endpoint
python -m somaos.bench.experiments.agent_directed --replay runs/walk.jsonl

# ตอนนี้ (ยังไม่มี endpoint) — ใช้ตัวแทนที่เขียนสคริปต์ไว้
python -m somaos.bench.experiments.agent_directed
```

รองรับ `/v1/chat/completions` ซึ่ง **ollama, llama.cpp, vLLM, LM Studio เสิร์ฟเหมือนกันหมด**
ใช้ `urllib` ของ stdlib ไม่เพิ่ม dependency

---

## 2. สิ่งที่โมเดลเห็นจริง ๆ

```
You are recalling a memory by walking a tree of memories.
You are standing at one memory. You can move to a related one, step back to a
broader one, bring the current one to mind, or stop.
Moving uses effort. Bringing a memory to mind uses none, so bring anything
useful to mind as you pass it.
Answer with the number of one option and nothing else.

You are at this memory:
  about: incident
  note: the incident stretch
  when: 20 to 20

Effort left: 8 moves.  Brought to mind: 0 (room for 8 more, at no cost in effort).

Options:
  1. Go into a memory within this one: nin, server, outage -- the night the server fell over
  2. Go to a related memory: routine -- the routine stretch
  3. Bring this memory to mind.
  4. Stop -- I have what I need.

Which number?
```

**~156 tokens ต่อครั้ง** และมีสามข้อที่ตั้งใจออกแบบ:

**ไม่มี address hex ใน prompt เลย** `describe()` ให้ address 71 ตัวอักษร
ถ้าให้ gemma 4B คัดกลับมา **มันจะพลาด** — หายไปตัวนึง หรือแต่งขึ้นมาให้ดูเหมือนจริง
แล้วทุกคำตอบแบบนั้นจะกลายเป็น off-menu ทั้งหมด → prompt แสดงเลข 1-4 แล้วโมดูลนี้แปลงกลับเอง

**บอกเศรษฐศาสตร์ให้ชัด** ว่า *เดินเสียแรง หยิบไม่เสีย* — ถ้าไม่บอก โมเดลจะเดาว่าทุกอย่างเสียเท่ากัน
แล้วหวงก้าวจนไม่หยิบอะไรกลับมาเลย (ตัวแทนที่เขียนสคริปต์ไว้เป็นแบบนั้นจริง ๆ ก่อนแก้)

**เห็นแค่จุดที่ยืนอยู่กับเพื่อนบ้าน ไม่เห็นทั้งคลัง** — prompt ที่ลิสต์ทุกอย่างไม่ใช่การนำทาง

---

## 3. อ่านคำตอบยังไง — ยืดหยุ่นเรื่องรูปแบบ เข้มเรื่องผลลัพธ์

รับได้หมด: `2` · `Option 2` · `I choose 2` · `CHOICE: 2` · `{"choice": 2}` ·
` ```json {"choice":2} ``` ` · `**2**` · `Answer: 2\n\nBecause...` ·
`stop` · `I think we should stop.`

ปฏิเสธ: `99` · `""` · `teleport` · `{"choice": 0}` · `{"move":"descend"}` (กำกวม มี 4 ตัวเลือก)

**⚠️ บั๊กที่เจอตอนเขียน test และสำคัญมาก:** เดิม `no` อยู่ในรายการคำที่แปลว่า "หยุด"
แล้วค้นหาทั้งประโยค → **`"no idea"` ถูกอ่านเป็น "ตัดสินใจหยุด"**
โมเดลที่เพิ่งบอกว่ามันงง จะถูกบันทึกว่ามันตัดสินใจแล้ว —
**การทดลองที่ถามว่า "โมเดลนำทางเก่งไหม" จะนับความสับสนเป็นความเด็ดขาด**
แก้เป็นแยกสองชุด: คำกริยา (`stop`/`done`/`finish`) หาได้ทั้งประโยค ส่วน `no`/`none`/`nothing`
ต้องเป็นคำตอบทั้งอันเท่านั้น

---

## 3.5 ทางเดิน HTTP — วิ่งจริงแล้ว ไม่ใช่แค่ผ่าน stub

ทุก test ก่อนหน้านี้เข้าถึงโมเดลผ่าน callable (`StubModel`) ซึ่งเป็นสิ่งที่ทำให้ harness ทดสอบได้
แต่ก็แปลว่า**ชิ้นเดียวที่วิธีนั้นแตะไม่ถึงคือ HTTP request เอง** — ซึ่งเป็นชิ้นแรกที่ endpoint จริงจะเจอ
ตอนนี้จึงมี `http.server` ของ stdlib ตอบด้วย wire format เดียวกัน วิ่งผ่าน loopback socket จริงใน process:

| ตรวจอะไร | test |
|---|---|
| base URL → `/v1/chat/completions` (มี/ไม่มี `/` ท้าย, ส่ง path เต็มมาแล้ว) | `test_a_base_url_becomes_the_chat_completions_path` |
| body ถูกต้อง · `temperature=0` · `Authorization: Bearer` · นับ `calls`/`seconds` | `test_the_client_actually_speaks_to_an_endpoint` |
| การนึกทั้งครั้งขับผ่าน socket จริงได้ | `test_a_whole_recall_can_be_driven_over_http` |
| endpoint ไม่มีอยู่ → `ModelError` ไม่ใช่กลืนเงียบ | `test_an_endpoint_that_is_not_there_is_reported_not_swallowed` |
| endpoint ตอบ 200 แต่ไม่มี content (เช่น model not found) → บอกตรง ๆ | `test_an_endpoint_that_answers_with_nothing_usable_says_so` |

**บั๊กที่เจอตอนวิ่งของจริงผ่าน endpoint ปลอมทั้งการทดลอง:** `recovered` ถูกนับ **สองครั้ง**
ในเส้นทาง "โมเดลแต่ง address ขึ้นมาเอง" — `_consult()` เครดิตให้ตอนที่ถูกเรียกพร้อม `reason`
แล้ว caller เครดิตซ้ำอีกที ผลคือรายงานว่าแก้ตัวสำเร็จ 2 ครั้งจากความผิด 1 ครั้ง
(เห็นชัดตอนรันจริง: `off_menu` 28.3 แต่ `recovered` 56.7)

เส้นทางนี้ต่างจาก "ตอบท่าที่ไม่มีอยู่" ตรงที่**คำตอบ parse ผ่าน** มีแต่ machine ที่ปฏิเสธทีหลังหนึ่งชั้น
จึงไม่มี test ตัวไหนคุมอยู่เลย แก้แล้ว + `test_an_invented_address_is_one_mistake_and_one_recovery`
คุมไว้ **ตัวนับนี้สำคัญเพราะมันคือคำตอบว่า "โมเดลตัวนี้ใช้กับ retry ได้ไหม"** — นับเกินคือรายงานว่า
โมเดลรับคำแก้ได้ดีกว่าความจริง

---

## 4. โมเดลตอบพลาดแล้วเกิดอะไร

| อาการ | ผล | ตัวนับ |
|---|---|---|
| ตอบนอกเมนู | **ส่งเมนูกลับไปพร้อมบอกว่าผิดตรงไหน แก้ได้ 2 ครั้ง** | `off_menu` / `recovered` |
| แก้แล้วถูก | เดินต่อตามปกติ | `recovered` |
| แก้แล้วยังผิด | จบด้วยของที่หาได้แล้ว | `off_menu` |
| เลือกถูกกฎแต่ไม่คืบ | หยุดหลัง 3 ครั้งติด | `stalls` |
| endpoint ล่ม / timeout | จบการเดิน ยังตอบได้ | — |

`--on-error raise` ตอนทดลองที่ต้องการความเข้มงวด: การวัดว่า "ให้ LLM เลือกเองดีกว่าไหม"
**ห้ามกลืนความผิดพลาดของโมเดลเงียบ ๆ** ไม่งั้นจะกลายเป็นการวัดการกลืน

---

## 5. ผลตอนนี้ (ตัวแทนสคริปต์ ยังไม่ใช่โมเดลจริง)

3 seeds · 200 วัน · store 200 KB · context 256 tokens · **effort 8**

| | detail | gist | habit | เทียบ/คำถาม | เรียกโมเดล/คำถาม |
|---|---|---|---|---|---|
| fast path (control) | **0.887** | 1.000 | 1.000 | 99.9 | — |
| agent-directed | 0.701 | 1.000 | 1.000 | 97.6 | 5.00 |

**นี่คือพื้น ไม่ใช่ผลของโมเดล** ตัวแทนสคริปต์แค่ "เดินสลับหยิบ" ไม่ได้อ่านความหมายอะไรเลย
มีไว้เพื่อพิสูจน์ว่า **harness แยกแยะได้จริง** — chooser ที่แย่ต้องได้คะแนนแย่
ถ้า gemma ทำได้ไม่ถึง 0.701 แปลว่ามันแย่กว่าการเดินสุ่มมีระบบ

**เพดานของทาง agent-directed ตรวจแล้วว่าไม่ตัน** — chooser ที่เขียนให้ฉลาดขึ้นทำได้
**detail 0.800 โดยใช้แค่ 2 ก้าว** เทียบกับ fast path 0.880 ที่ใช้ 8 ก้าว
แปลว่าถ้าโมเดลเก่งพอ มันชนะได้ **ปัญหาถ้าเกิด จะอยู่ที่โมเดล ไม่ใช่ที่โครงสร้าง**

---

## 5.5 ★ ผลจริงจาก Typhoon (`typhoon-v2.5-30b-a3b-instruct`, 1 ก.ย. 2026)

**⚠️ นี่คือตัวเลขจากคนละจุดเวลากัน ไม่ใช่รอบวัดเดียวหลังแก้ครบ — อย่าอ่านเป็นผลสุดท้าย**

การต่อกับ endpoint จริงครั้งแรก (3 dev seeds, ก่อนแก้บั๊กใดๆ ในตารางด้านล่าง):

| | detail | gist | เรียกโมเดล/คำถาม | stalls |
|---|---|---|---|---|
| fast path (control) | 0.887 | 1.000 | — | — |
| **Typhoon (ครั้งแรก)** | **0.0133** | 0.9625 | 7.55 | 0 |

**Typhoon เดินถูกกฎ ไม่หยุดกลางทาง แต่แทบไม่เคยเจาะลงไปหารายละเอียดเลย** ("navigates legally and
will not dig") — ตัวเลข detail ต่ำขนาดนี้ต้องรายงานตรงๆ ไม่ใช่ผลดี

**บั๊ก 5 ตัวที่เจอตอนต่อจริง (การทดสอบด้วยตัวแทนสคริปต์มองไม่เห็นสักตัว) — แก้แล้วทั้งหมด:**

1. `base_url` ที่ลงท้ายด้วย `/v1` อยู่แล้วถูกต่อซ้ำเป็น `/v1/v1` → 404 ทุกครั้ง
2. เมนูยังเสนอ MATERIALIZE ซ้ำที่จุดที่ดึงเข้ามาแล้ว — Typhoon ทำตามที่ prompt บอกว่า "หยิบไม่เสียแรง"
   จนโดนตัดเพราะค้าง (**184 stalls / 63 คำถาม, detail 0.000** ก่อนแก้) พอปิดท่านี้: **0 stalls, gist
   0.9016 → 0.9508** บน seed เดียวกัน
3. `recovered` ถูกนับซ้ำสองครั้งตอนโมเดลแต่ง address ขึ้นมาเอง — รายงานว่าแก้ตัวเก่งเกินจริง
4. เมนู LATERAL ไม่ตรงกับตำแหน่งจริงหลัง DESCEND ให้เดินตามที่โมเดลเลือกเอง — พรอมต์ภาษาไทยโดนหนักสุด
   **4.33 off-menu ต่อคำถาม**
5. tool calling วัดได้ **0 ครั้งจาก 3,019 การแลกเปลี่ยน** เพราะบรรทัดสุดท้ายของ prompt ขัดกับ schema ที่เปิดไว้
   — แก้คำสั่งแล้ว Typhoon เรียก tool ได้ถูกรูปแบบทั้งอังกฤษและไทย (ข้อสังเกต: `tool_choice="required"`
   endpoint รับพารามิเตอร์แต่เพิกเฉย ยังตอบเป็นข้อความได้อยู่ดี)

**ที่ยังไม่มี:** รอบวัดเดียวที่ครบทั้ง 3 dev seeds **หลังแก้บั๊กทั้ง 5 ตัวพร้อมกัน** — เลข detail
0.0133 ข้างบนน่าจะดีขึ้นหลังแก้บั๊ก #2 (ตัวที่กระทบ stalls/gist แรงสุด) แต่ **ยังไม่ได้วัดยืนยัน**
ต้องรันซ้ำก่อนถึงจะเอาไปอ้างเป็นตัวเลขทางการได้

---

## 6. ความไม่เท่าเทียมที่ต้องพูดตรง ๆ

**fast path หยิบความทรงจำ 8 ชิ้นตอนจบ "ฟรี"** เรียงตามคะแนน ในก้าวเดียว
**ส่วน agent ต้องสั่งหยิบทีละชิ้น = เรียกโมเดล 1 ครั้งต่อชิ้น**

นี่ไม่ใช่บั๊ก มันคือ**ราคาของการให้ agent คุมเอง** และต้องรายงานไว้ ไม่ใช่ออกแบบเลี่ยง
ถ้าอยากให้เท่ากันจริง ต้องเพิ่มท่า "หยิบ N ตัวที่ดีที่สุดแล้วจบ" ให้ agent เลือกได้
— **ยังไม่ได้ทำ เพราะมันเปลี่ยนคำถามที่กำลังวัดอยู่**

`recall_ops` ตั้งไว้ **8 ไม่ใช่ 32** เพราะทุกก้าวคือการเรียกโมเดล
ที่ 32 คำถามเดียวอาจยิง 32 รอบ — และ fast path ก็วัดที่ 8 เท่ากันเพื่อความเป็นธรรม

---

## 7. ยังไม่มี

| ขาด | ผลกระทบ |
|---|---|
| **embedding จริง** | ยังใช้ hash · ให้ typhoon เลือกทางได้เลย แต่ "คล้ายกัน" ยังไม่ใช่ความหมายจริง |
| **จูนค่าใหม่หลังเปลี่ยน embedder** | `COHERENCE` / `MAX_CHILDREN` / `STRENGTH_WEIGHT` ตั้งบนเรขาคณิต hash |
| **รันรวมเบ็ดเสร็จหลังแก้บั๊กครบ 5 ตัว** | ตัวเลข detail/gist ใน §9 มาจากคนละจุดเวลากัน ยังไม่มีรอบเดียวที่วัดพร้อมกันทั้งหมดหลังแก้ทุกอย่างแล้ว |
| **วัด latency จริง** | client นับ `seconds` ไว้แล้ว แต่ยังไม่มีตัวเลขจริงตีพิมพ์ |

**ที่ตัดออกจากลิสต์นี้แล้ว (ทำและวัดจริงแล้ว วันที่ 1 ก.ย.):** ท่า "หยิบทีเดียวหลายชิ้น" (`GATHER`,
ดู §6) · prompt ภาษาไทย (`--lang th`) · tool calling (§4, `--tools`)

---

## 8. ไฟล์

| ไฟล์ | หน้าที่ | มี network ไหม |
|---|---|---|
| `broker/recall/prompting.py` | view → prompt · reply → move | **ไม่มี** |
| `broker/recall/navigator.py` | ขับการเดิน กันพลาด นับสถิติ | **ไม่มี** |
| `bench/modelclient.py` | คุย endpoint · บันทึก · replay | มี (urllib) |
| `bench/experiments/agent_directed.py` | การทดลองเทียบ | ผ่าน client |

**broker ไม่แตะ network เลย** แอปที่ฝัง SomaOS ไม่ต้องรับภาระเรื่อง HTTP
และ `somaos/modelbus/` ยังไม่ถูกสร้าง (ตาม scope ใน CLAUDE.md)
