# 08_TYPHOON.md — โมเดลที่ใช้ทดลอง

> 🔑 **ห้ามมี API key ใน repo** — รับผ่าน env เท่านั้น

## ใช้อันนี้

```bash
export SOMAOS_MODEL_ENDPOINT=https://api.opentyphoon.ai/v1
export SOMAOS_MODEL=typhoon-v2.5-30b-a3b-instruct
export SOMAOS_MODEL_API_KEY=<secret>
```

`typhoon-v2.5-30b-a3b-instruct` คือ **โมเดลแชทตัวเดียวบน endpoint นี้** (ที่เหลือเป็น OCR/ASR)
เทียบข้ามโมเดลไม่ได้ · OpenAI-compatible · ฟรี · ~0.4 วินาที/call

## รัน

```bash
python -m somaos.bench.experiments.agent_directed --lang th --gather --record runs/th.jsonl
```

flags: `--lang en|th` · `--tools` · `--gather` · `--seeds` · `--replay <file>`
ไม่ต้องใส่ endpoint/model/key ถ้าตั้ง env แล้ว · หนึ่งตัวแปร = ~1,100 calls ≈ 5-10 นาที
`--replay` ตรวจซ้ำได้โดยไม่ต้องมี endpoint (เทียบ prompt ไม่ใช่แค่นับ — replay ที่เพี้ยนจะ error)

## 4 อย่างที่จะเสียเวลาถ้าไม่รู้

1. **เพดาน 8,192 token รวม prompt + completion** (หน้า models โฆษณา 128K — ไม่จริง)
   ตอนนี้ใช้ ~300 token/call ยังห่างมาก แต่ถ้าจะใส่ history ต้องคิด
2. **`tool_choice: "required"` ถูกเพิกเฉย** — ยังตอบเป็นข้อความ tool calling ต้องพึ่ง prompt
3. **prompt ต้องขอให้เรียก tool** ถ้าเปิด `--tools` (`render_prompt(tools=True)` จัดการให้แล้ว)
   เคยลืม → ได้ tool call ศูนย์ครั้งจาก 3,019 exchanges และเกือบรายงานว่าโมเดลเรียก tool ไม่เป็น
4. **ไม่มี JSON mode / `response_format`** — ทางเดียวที่บังคับรูปแบบได้คือ tool calling

## เอกสารทางการเชื่อไม่ได้

หน้า tool calling ใช้ `typhoon-v2.1-12b-instruct` เป็นตัวอย่าง ซึ่ง **ไม่มีบน endpoint นี้**
และตาราง capability เว้นช่อง tool calling ว่างทุกโมเดล ทั้งที่ v2.5-30b รองรับจริง
→ **ยิง `GET /v1/models` และทดสอบเองก่อนเสมอ**

## production

จะเป็นคนละ endpoint · `ChatModel` ไม่ผูกกับ Typhoon เปลี่ยนแค่ env 3 ตัว
