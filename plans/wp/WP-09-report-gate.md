# WP-09 — Report generator + kill-criteria gate

**Depends on:** WP-08  **Blocks:** WP-10  **Est:** 1 d

## Goal
`bench/gate.py` + `bench/report.py` — เปลี่ยน §7.4 จากวิจารณญาณให้เป็นโค้ดที่ตอบ PASS/FAIL

## 1. `gate.py` — kill criteria เป็นฟังก์ชัน

```python
@dataclass(frozen=True, slots=True)
class GateResult:
    id: str                 # "KC1".."KC4"
    passed: bool
    value: float | None
    threshold: float
    detail: str

def evaluate_gates(rows: list[dict], timing: list[dict], cfg: dict) -> list[GateResult]
def phase0_verdict(results: list[GateResult]) -> Literal["PASS","FAIL"]   # FAIL ถ้าข้อใดข้อหนึ่ง fail
```

| id | เกณฑ์ (§7.4) | implementation |
|---|---|---|
| **KC1** | `S` ต้องชนะ `B2` อย่างมีนัยสำคัญที่ budget เท่ากัน | paired bootstrap (10,000 resamples) บน **holdout seeds**, จับคู่ตาม (regime, budget, tau, seed) → รายงาน mean Δstrict_recall + 95% CI. ผ่านเมื่อ CI ล่าง > 0 **และ** Δ ≥ `min_effect` (default 0.05) |
| **KC2** | `competitive_ratio ≥ 0.7` | ใช้ **regime `uniform` + mode `exact_belady`** เท่านั้น (D-09) เอา median ข้าม holdout seeds ที่ budget กลาง (4096) |
| **KC3** | fast path ≤ 5% ของ compute รวม | `p95(fast_path_ms_per_tick) ≤ budget_ms_per_tick` จาก D-07 |
| **KC4** | surprise correlate กับความสำคัญจริง | spearman(surprise, ground_truth_utility) บน regime ที่ **ไม่ใช่** `adversarial_flat` → ต้อง > 0.25 และ p < 0.01 |

**หมายเหตุ KC1:** `adversarial_flat` ถูก **ยกเว้น** จากการตัดสิน KC1 แต่ต้องรายงานแยก
ถ้า S ชนะ B2 ใน `adversarial_flat` ด้วย → gate ต้องขึ้น **warning "SUSPECTED LEAK"** (WP-02 §5.1)

## 2. `report.py`
```
python -m somaos.bench.report --in runs/ --out runs/report.md
```
ออกทั้ง `report.md` (คนอ่าน) และ `report.json` (เครื่องอ่าน)

โครง `report.md`:
```
PHASE0 GATE: PASS|FAIL          ← บรรทัดแรกเสมอ
  KC1 ... PASS  Δ=+0.13 [95% CI 0.09, 0.17]  vs B2
  KC2 ... FAIL  competitive_ratio=0.61 < 0.70
  ...
[WARNINGS]
  - B4 เป็น cost-model proxy ไม่ใช่ MemGPT จริง (master plan §3.4)
  - competitive_ratio ของ regime X เทียบ upper bound → เป็นค่าต่ำกว่าจริง (D-09)
## 1. Headline table   (holdout only)  policy × budget: strict_recall, tokens/query, comp_ratio
## 2. Per-regime table
## 3. Cost vs quality frontier (ตัวเลข ไม่ต้องมีกราฟ)
## 4. Sensitivity ต่อ tau (D-03)
## 5. Dev-set table (แยกชัดว่าไม่ใช้ตัดสิน)
## 6. Reproduction: config hash, trace ids, คำสั่งที่ใช้
```

**ข้อบังคับ:** WARNINGS ทุกข้อในตารางข้างบนต้องถูกพิมพ์ทุกครั้ง ห้ามซ่อนเมื่อผลออกมาดี

## Acceptance
- gate ทำงานบน fixture rows ที่ประดิษฐ์ขึ้น: เคส PASS ทั้งหมด, เคสที่ KC2 fail, เคส leak warning
- `report.md` บรรทัดแรก match regex `^PHASE0 GATE: (PASS|FAIL)$`
- `report.json` มี `gates[]`, `warnings[]`, `tables{}`, `provenance{}`
- report สร้างจาก JSONL อย่างเดียว — ห้าม re-run policy (ตรวจว่า import ของ report.py ไม่มี broker.policies)

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-09-report-gate.md`
> gate ต้องตัดสินจากตัวเลขล้วน — ห้ามใส่ logic ที่ "ผ่อนผัน" เกณฑ์
> ถ้าผลจริงออกมา FAIL ให้รายงาน FAIL พร้อม hypothesis ว่าทำไม แล้วหยุด อย่าปรับ threshold หรือ weight (§15 ข้อ 7)
