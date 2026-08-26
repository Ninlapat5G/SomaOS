# WP-02 — Synthetic trace generator + ground-truth world

**Depends on:** WP-01  **Blocks:** WP-07, WP-08  **Est:** 2 d
**นี่คือ WP ที่เสี่ยงที่สุดต่อความน่าเชื่อถือของทั้ง Phase 0 — อ่าน §5 ให้ครบก่อนเขียนโค้ด**

## Goal
สร้าง long-horizon trace แบบ deterministic ไม่มี LLM ที่ **มี ground truth 100%**
ว่า query แต่ละอันต้องใช้ memory item ชิ้นไหนบ้าง

## 1. World model (`bench/trace/world.py`)

```
Topics    : T หัวข้อ (default 24) มี co-occurrence matrix
Entities  : E ตัวตน (default 60) แต่ละตัวมี topic affinity vector
Facts     : ข้อเท็จจริง (entity, topic, value) ที่ "เป็นจริง" ณ ช่วงเวลาหนึ่ง
            fact แก้ไขได้ → เกิด revision ทำให้ item เก่า stale
Predictor : distribution ที่ world ใช้ "ทำนาย" observation ถัดไป
            → surprise = 1 - P(obs | predictor), novelty = 1 ถ้าไม่เคยเห็น (entity,topic) นี้
```

`surprise`/`novelty` คำนวณจาก **world ไม่ใช่จาก policy** → ทุก policy เห็นค่าเดียวกัน (fair comparison)

## 2. Trace generation

ต่อ tick:
1. สุ่มจำนวน observation (Poisson, seeded stream `"obs"`)
2. แต่ละ observation → `MemoryItem` (kind, tokens, topics, entities, surprise, novelty)
3. ด้วยความน่าจะเป็น `p_query` → สร้าง `Query`
   - เลือก "คำถาม" จาก fact ที่ยังเป็นจริง ณ tick นี้
   - `required_item_ids` = item ที่เป็นหลักฐานล่าสุดของ fact นั้น (**ground truth**)
   - ระยะห่างเวลา (recency gap) ระหว่าง query กับ item ที่ต้องใช้ = ตัวแปรควบคุมสำคัญ

## 3. Regimes (ต้องมีครบทุกตัว ไม่ใช่เลือกเฉพาะที่ policy เราเก่ง)

| id | ลักษณะ | ทดสอบอะไร |
|---|---|---|
| `uniform` | ทุก item tokens เท่ากัน, query ถามของที่เพิ่งเกิด | baseline + ใช้คำนวณ **OPT-exact** (D-09) |
| `variable` | tokens แปรผัน (lognormal) | ความสมจริง + OPT-UB |
| `long_gap` | query ถามของที่เกิดไปแล้ว 500–2000 ticks | จุดที่ B1 sliding window ต้องแพ้ |
| `bursty` | observation มาเป็นคลื่น สลับช่วงเงียบ | thrashing / churn |
| `high_noise` | 80% ของ observation เป็น low-surprise ซ้ำซาก | จุดที่ surprise gating ต้องได้เปรียบ |
| `adversarial_flat` | **surprise ไม่ correlate กับ required เลย** (สุ่มอิสระ) | ← ดู §5 |
| `topic_drift` | goal topic เปลี่ยนทุก ~300 ticks | relevance term |

## 4. Public API (`bench/trace/generator.py`)

```python
@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    regime: str
    seed_root: str
    n_ticks: int = 5000
    n_topics: int = 24
    n_entities: int = 60
    obs_per_tick_lambda: float = 3.0
    p_query: float = 0.08
    token_mean: int = 120
    fact_revision_rate: float = 0.02
    # ... regime overrides

def generate(cfg: GeneratorConfig) -> Trace
def ground_truth_utility(trace: Trace) -> dict[str, float]
    """สำหรับแต่ละ item id → จำนวนครั้งที่มันถูก required ในอนาคต (ใช้ใน kill criterion ข้อ 4)"""
```

## 5. มาตรการกัน "validate ตัวเองในสุญญากาศ" — บังคับ

1. **`adversarial_flat` regime ต้องมี** และต้องรายงานในตารางผลเสมอ
   ถ้า `S` ชนะ `B2` แม้ใน regime นี้ → สงสัยว่ามี bug/leak ใน harness ไม่ใช่ข่าวดี
   (`S` **ควร** เสมอกับ B2 ที่นี่ เพราะ surprise ไม่มีข้อมูล)
2. **Pre-registration**: parameter ของทุก regime ต้อง commit ลง `bench/configs/regimes.json`
   **ก่อน** implement `s_soma.py` (WP-06) — ห้ามแก้หลังเห็นผลของ S
   ถ้าจำเป็นต้องแก้ → บันทึกใน `plans/CHANGELOG_REGIMES.md` พร้อมเหตุผลและวันที่
3. **ห้าม generator รู้จัก policy ใด ๆ** — `bench/trace/` ห้าม import จาก `somaos/broker/policies/`
   ต้องมี test บังคับข้อนี้ (`test_no_policy_import_in_generator`)
4. **Sanity check ที่ต้องผ่าน**: B0 (full context) ต้องได้ `strict_recall == 1.0` ทุก regime
   ถ้าไม่ใช่ → ground truth ผิด ไม่ใช่ policy แย่

## 6. Acceptance
- `generate(cfg)` เรียกซ้ำด้วย cfg เดิม → `trace_id` เท่ากัน และ events เท่ากันทุกฟิลด์
- seed ต่างกัน → trace ต่างกัน (test แบบ statistical ไม่ใช่ exact)
- ทุก `Query.required_item_ids` ⊆ item ids ที่ปรากฏก่อนหน้า tick ของ query (ห้ามถามอนาคต)
- ใน `long_gap`: median(query.tick - max(required item.created_tick)) ≥ 500
- ใน `high_noise`: ≥ 75% ของ items มี `surprise < 0.2`
- ใน `adversarial_flat`: |spearman(surprise, ground_truth_utility)| < 0.05
- test บังคับ §5.3

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-02-trace-generator.md`
> ให้ความสำคัญกับ §5 เป็นพิเศษ — มันคือสิ่งที่ทำให้ผล Phase 0 เชื่อถือได้หรือไม่ได้
> commit `bench/configs/regimes.json` ให้เสร็จก่อนแตะ WP-06
> ห้าม import อะไรจาก `somaos/broker/policies/` เข้ามาใน `bench/trace/`
