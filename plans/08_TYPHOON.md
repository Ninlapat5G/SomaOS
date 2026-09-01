# 08_TYPHOON.md — โมเดลที่ใช้ทดลอง และวิธีรันโดยไม่ต้องเปิดคอมทิ้งไว้

> **สถานะ:** ต่อจริงแล้ว รันครบแล้วบน dev seeds
> **ทุกอย่างในไฟล์นี้ยืนยันด้วยการยิงจริง** ไม่ได้คัดจากเอกสารทางการอย่างเดียว
> — เพราะเอกสารทางการ **ไม่ตรงกับของจริงหลายจุด** (§2)
> 🔑 **ไม่มี API key ในไฟล์นี้และห้ามมีในไฟล์ไหนใน repo** ดู §5

---

## 1. endpoint และสิ่งที่มีจริง

```
base URL : https://api.opentyphoon.ai/v1
protocol : OpenAI-compatible /v1/chat/completions
auth     : Authorization: Bearer <key>
```

`ChatModel` ของเรารองรับ base URL ทั้งแบบลงท้าย `/v1` และไม่ลงท้าย (เคยเป็นบั๊ก `/v1/v1/…` แก้แล้ว มี test คุม)

**โมเดลที่มีจริงบน endpoint นี้** (จาก `GET /v1/models` ไม่ใช่จากเอกสาร):

| model id | ใช้ทำอะไร |
|---|---|
| `typhoon-v2.5-30b-a3b-instruct` | **โมเดลแชทตัวเดียวที่มี** — 30B MoE (active 3B) |
| `typhoon-ocr-v1.5` · `typhoon-ocr` · `typhoon-ocr-preview` | OCR |
| `typhoon-asr-realtime` · `typhoon-isan-asr-realtime` | ถอดเสียง |

→ **เทียบข้ามโมเดลบน endpoint นี้ไม่ได้** มีตัวเลือกเดียว

---

## 2. ⚠️ จุดที่เอกสารทางการไม่ตรงกับของจริง

| เอกสารบอก | ของจริง |
|---|---|
| หน้า tool calling ใช้ `typhoon-v2.1-12b-instruct` เป็นตัวอย่าง | ยิงแล้วได้ **`{"detail":"Model not found"}`** — ไม่มีบน endpoint นี้ |
| ตาราง capability **เว้นช่อง tool calling ว่างทุกโมเดล** | `typhoon-v2.5-30b-a3b-instruct` **รองรับ tool calling จริง** (§3) |
| หน้า models โฆษณา context **128K** | หน้า API reference ระบุ **8,192 tokens รวม prompt + completion** — ยึดตัวหลัง |

**บทเรียน:** ก่อนออกแบบการทดลองรอบหน้า ให้ยิง `GET /v1/models` และทดสอบ tool calling จริงก่อนเสมอ อย่าเชื่อตาราง

---

## 3. Tool calling — ใช้ได้ ยืนยันแล้ว

ยิงจริงด้วย `tools` + `tool_choice: "auto"` แล้วได้:

```
finish_reason : tool_calls
content       : None
tool_calls    : [{"function": {"name": "walk",
                  "arguments": "{\"move\": \"lateral\", \"option\": 2}"},
                  "id": "call_0", "type": "function"}]
```

รูปแบบ schema เป็นแบบ OpenAI ทุกประการ (`type`/`function.name`/`function.description`/`function.parameters`)

**ทำไมเราสนใจ:** `build_tools()` ใส่เมนูเป็น `enum` ของหมายเลขตัวเลือก
→ **เซิร์ฟเวอร์ที่ตรวจ argument ตาม schema จะคืนตัวเลือกนอกเมนูไม่ได้เชิงโครงสร้าง**
= ตัด "โมเดลจัดรูปแบบคำตอบถูกไหม" ออกจากตัวแปร เหลือแค่ "มันนำทางเป็นไหม"
(ดู `plans/07_LLM_HARNESS.md` §3.5 · โมเดลที่ตอบเป็นข้อความแทนการเรียก tool ยังอ่านได้ แต่นับไว้ใน `text_instead_of_call`)

---

## 4. พารามิเตอร์และข้อจำกัดที่ต้องรู้

| เรื่อง | ค่า |
|---|---|
| **เพดาน token** | **8,192 รวม prompt + completion** — prompt เราต่อครั้ง ~1,100 ตัวอักษร (~300 token) ยังห่างมาก แต่ถ้าจะใส่ history หรือทำ bulk ต้องคิด |
| latency | **~0.4 วินาที/call** เร็วมาก |
| รับ | `model` `messages` `max_tokens` `temperature` `top_p` `n` `stream` `stop` `presence_penalty` `frequency_penalty` `repetition_penalty` `user` |
| ไม่มี | JSON mode / `response_format` / guided decoding / `logit_bias` — **ทางเดียวที่จะบังคับรูปแบบคือ tool calling** |
| เอกสารแนะนำ | `repetition_penalty = 1.05` (เรายังไม่ได้ใช้ — ดู §7) |

**ค่าที่เราใช้และเหตุผล:** `temperature = 0` (การเดินที่ทำซ้ำไม่ได้เทียบกับ searcher ที่ deterministic ไม่ได้)
· `max_tokens = 16` สำหรับข้อความ (คำตอบคือตัวเลข) · `128` สำหรับ tool call (JSON ยาวกว่า และ tool call ที่ถูกตัดกลางคัน
แยกไม่ออกจากโมเดลที่เรียก tool ไม่เป็น)

**ต้นทุนต่อการทดลองหนึ่งตัวแปร** (3 seeds × 63 คำถาม): **~1,100 model calls · ~570 วินาทีที่รอโมเดล**

---

## 5. 🔑 API key — ห้ามลง repo

รับผ่าน **environment variable** ทั้งหมด ไม่ต้องพิมพ์ลง command line:

```bash
export SOMAOS_MODEL_ENDPOINT=https://api.opentyphoon.ai/v1
export SOMAOS_MODEL=typhoon-v2.5-30b-a3b-instruct
export SOMAOS_MODEL_API_KEY=<key>
```

แล้วรันสั้น ๆ ได้เลย:

```bash
python -m somaos.bench.experiments.agent_directed --lang th --gather
```

**ทำไมต้อง env var ไม่ใช่ `--api-key`:** key ที่ส่งเป็น argument จะไปโผล่ใน shell history,
ใน process listing และใน log ของ runner — ซึ่งไม่ใช่ที่ของ credential
`--api-key` ยังใช้ได้อยู่ แต่ env var เป็นทางที่ควรใช้

> ⚠️ key ที่ใช้ทดลองรอบนี้ถูกส่งมาเป็น plaintext ในแชท **ควรถือว่าหลุดแล้วและ rotate**
> `runs/` อยู่ใน `.gitignore` และ `RecordingModel` บันทึกแค่ prompt/reply ไม่เคยบันทึก key

---

## 6. รันบน cloud โดยไม่ต้องเปิดคอมทิ้งไว้

สิ่งที่ทำให้รันแบบไม่มีคนเฝ้าได้:

1. **ไม่ต้องมี argument เลย** — endpoint/model/key อ่านจาก env (§5)
2. **บันทึก transcript ไว้เสมอ** `--record runs/<ชื่อ>.jsonl`
3. **ตรวจซ้ำทีหลังได้โดยไม่ต้องมี endpoint** `--replay runs/<ชื่อ>.jsonl`
   → ได้ตัวเลขเดิมทุกหลัก (ยืนยันแล้วกับ transcript 1,769 บรรทัด)
   `ReplayModel` เทียบ **prompt** ไม่ใช่แค่นับจำนวน ดังนั้น replay ที่เพี้ยนจะ error ไม่ใช่ตอบผิดเงียบ ๆ

รันครบทุกตัวแปรในคำสั่งเดียว:

```bash
for v in ":base" "--lang th:thai" "--tools:tools" "--gather:gather" "--lang th --gather:th_gather" "--lang th --tools --gather:all"; do
  flags="${v%%:*}"; name="${v##*:}"
  python -m somaos.bench.experiments.agent_directed $flags --record runs/$name.jsonl > runs/$name.json 2>&1
done
```

**ประมาณเวลา:** ~5-10 นาที/ตัวแปร → ครบ 6 ตัวราว 30-45 นาที
**ต้องมี:** `SOMAOS_MODEL_API_KEY` เป็น secret ของ environment นั้น · ไม่ต้องมี GPU · ไม่ต้องมี dependency นอก stdlib + numpy

---

## 7. ที่ยังไม่ได้ลอง

| ขาด | หมายเหตุ |
|---|---|
| `repetition_penalty = 1.05` | เอกสารแนะนำ เรายังใช้ default — ที่ `temperature=0` และคำตอบยาว 1 token น่าจะไม่ต่าง แต่ยังไม่ได้พิสูจน์ |
| streaming | ไม่ต้องใช้ คำตอบสั้นมาก |
| endpoint ของจริง (production) | จะเป็นคนละตัว — `ChatModel` ไม่ผูกกับ Typhoon เลย เปลี่ยนแค่ env 3 ตัว |
| embedding จริง | Typhoon ไม่มี embedding endpoint บน `/v1/models` ต้องหาที่อื่น — และ **ต้องรอหลัง A13** (`06_MODULE_API.md` §6) |
| หลายคำขอพร้อมกัน | ยังยิงทีละ call · ยังไม่รู้ rate limit เพราะยังไม่เคยชน |

---

## 8. ไฟล์ที่เกี่ยวข้อง

| ไฟล์ | หน้าที่ |
|---|---|
| `somaos/bench/modelclient.py` | `ChatModel.complete()` / `.call_tool()` · record · replay — **ที่เดียวที่แตะ network** |
| `somaos/broker/recall/prompting.py` | prompt (en/th) · `build_tools()` · parser — **ไม่แตะ network** |
| `somaos/bench/experiments/agent_directed.py` | การทดลองเทียบ + CLI |
| `plans/07_LLM_HARNESS.md` | ตัว harness เอง สิ่งที่โมเดลเห็น และบั๊กที่การต่อโมเดลจริงหาเจอ |
