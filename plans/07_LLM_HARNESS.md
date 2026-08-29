# 07_LLM_HARNESS.md — พร้อมต่อ LLM แล้ว

> **สถานะ:** ทำเสร็จและทดสอบครบ **ยังไม่เคยต่อโมเดลจริง**
> **ทดสอบ:** `tests/test_llm_harness.py` (40 ข้อ) · รวมทั้งโปรเจกต์ 672 ข้อ
> **ทุกอย่างรันได้แบบไม่ต้องมี endpoint** — พอ endpoint มา ถ้าผลออกมาแย่
> จะได้แปลว่า *โมเดลแย่* ไม่ใช่ *กาวไม่เคยถูกลอง*

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
| **embedding จริง** | ยังใช้ hash · ให้ gemma เลือกทางได้เลย แต่ "คล้ายกัน" ยังไม่ใช่ความหมายจริง |
| **จูนค่าใหม่หลังเปลี่ยน embedder** | `COHERENCE` / `MAX_CHILDREN` / `STRENGTH_WEIGHT` ตั้งบนเรขาคณิต hash |
| **ท่า "หยิบทีเดียวหลายชิ้น"** | ดู §6 |
| **prompt ภาษาไทย** | ตอนนี้อังกฤษล้วน · gemma อาจทำได้ดีกว่าถ้าเป็นไทย ต้องลอง |
| **วัด latency จริง** | client นับ `seconds` ไว้แล้ว แต่ยังไม่มีตัวเลขจริง |

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
