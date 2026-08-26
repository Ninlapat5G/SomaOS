# WP-08 — Metrics + benchmark runner

**Depends on:** WP-02, WP-05, WP-06, WP-07  **Blocks:** WP-09  **Est:** 1 d

## Goal
`bench/metrics.py` + `bench/runner.py` — รัน policy × regime × budget × seed แล้วออก JSONL
ตาม schema ใน `plans/02_INTERFACES.md` §6

## 1. Metric ที่ต้องคำนวณ (§7.3)
| กลุ่ม | metric |
|---|---|
| Cost | `tokens_per_query`, `total_tokens`, `llm_calls`, `llm_call_ratio` |
| Quality | `strict_recall`, `partial_recall` |
| Memory | `hit_at_k` (k=1,5,10), `context_churn_rate`, `thrash_indicator`, `encode_rate`, `evictions` |
| Oracle | `opt_strict_recall`, `opt_mode`, **`competitive_ratio = strict_recall / opt_strict_recall`** |
| Hypothesis | `surprise_utility_spearman` — spearman(item.surprise, ground_truth_utility) |

`competitive_ratio` เมื่อ `opt_strict_recall == 0` → คืน `null` ไม่ใช่ 0 หรือ inf

## 2. Runner
```
python -m somaos.bench.runner --config bench/configs/phase0.json [--out runs/]
```
- อ่าน config → cartesian product ของ axes → รันทุกช่อง
- แต่ละ run เขียน 1 บรรทัดลง `runs/results-<config_hash>.jsonl`
- timing เขียนแยก `runs/timing-<config_hash>.jsonl` (ไม่ deterministic)
- รองรับ `--jobs N` (multiprocessing) แต่ **ผลลัพธ์ต้องเท่ากับรัน serial** — มี test ยืนยัน
- เขียน `runs/config-<hash>.json` = config ที่ resolve แล้วทั้งหมด (reproducibility)

## 3. Leak prevention — `QueryView` (บังคับ, อ้างจาก WP-06 กฎข้อ 2)
```python
@dataclass(frozen=True, slots=True)
class QueryView:
    """สิ่งที่ policy เห็น — ไม่มี required_item_ids"""
    id: str; tick: int; topics: tuple[str,...]; entities: tuple[str,...]

def to_view(q: Query) -> QueryView
```
runner ส่ง `QueryView` ให้ policy เท่านั้น แล้วเก็บ `Query` ตัวเต็มไว้ให้ metrics/OPT
→ policy **ไม่มีทาง** เห็นเฉลยเชิงโครงสร้าง
มี test: `assert not hasattr(view, "required_item_ids")`

## 4. Dev / holdout split (D-10)
```json
"seeds": { "dev": ["dev-01","dev-02","dev-03"], "holdout": ["h-01", ..., "h-10"] }
```
- tune weight/config ได้บน `dev` เท่านั้น
- report ตัดสิน gate จาก `holdout` เท่านั้น
- runner ต้อง log ว่า run ไหนเป็น dev/holdout และ report ต้องแยกตาราง

## 5. Config schema (`bench/configs/phase0.json`)
```json
{
  "policies": ["B0","B1","B2","B3","B4","S"],
  "regimes": ["uniform","variable","long_gap","bursty","high_noise","adversarial_flat","topic_drift"],
  "budget_tokens": [1024, 2048, 4096, 8192],
  "tau_ticks": [8, 32, 128],
  "n_ticks": 5000,
  "seeds": {"dev": [...], "holdout": [...]},
  "opt": {"uniform": "exact_belady", "default": "upper_bound"},
  "cost_model": {"REF_LLM_CALL_MS": 800.0, "REF_TICK_LLM_CALLS": 0.1, "FAST_PATH_BUDGET_FRACTION": 0.05},
  "weights_file": "bench/configs/default_weights.json"
}
```

## Acceptance
1. รันสองครั้ง → `results-*.jsonl` identical bit-for-bit (`diff` ผ่าน)
2. `--jobs 4` ให้ผลเท่ากับ `--jobs 1` (sort บรรทัดก่อนเทียบ)
3. ทุกบรรทัดมีทุก field ตาม schema §6 (validate ด้วย test)
4. B0 ได้ `strict_recall == 1.0` ทุก regime — ถ้าไม่ใช่ runner ต้อง **fail ทันที** พร้อมข้อความชัด
5. ไม่มี policy ไหนได้ `competitive_ratio > 1.0` — ถ้ามี ให้ raise `OracleViolation`
6. `print()` ไม่ปรากฏใน bench/ ยกเว้นใน `__main__` ที่พิมพ์ path ของไฟล์ผล (§15 ข้อ 6)

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-08-metrics-runner.md`
> `QueryView` ใน §3 คือสิ่งที่ทำให้ผลการทดลองเชื่อถือได้ ทำก่อนอย่างอื่น
> acceptance ข้อ 4 และ 5 ให้ implement เป็น hard failure ไม่ใช่ warning
