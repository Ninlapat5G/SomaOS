# WP-10 — Determinism, property tests, CI

**Depends on:** WP-09  **Blocks:** —  **Est:** 1 d

## Goal
ทำให้ข้อรับประกันของ Phase 0 เป็น test ที่รันซ้ำได้ ไม่ใช่คำสัญญาใน doc

## 1. `tests/test_determinism.py`
- รัน `runner` ด้วย config ย่อ (n_ticks=300, 2 seeds) สองครั้ง → JSONL identical
- รันด้วย `PYTHONHASHSEED=0` และ `=1` → ผลเท่ากัน (subprocess)
- `--jobs 1` vs `--jobs 4` → เท่ากัน
- ทุก policy: รัน trace เดิม 2 รอบใน process เดียว (มี `reset()` คั่น) → `bundle_hash` sequence เท่ากัน

## 2. `tests/test_invariants.py` (property-based, ใช้ `random` seeded ไม่ต้องพึ่ง hypothesis)
- bundle ไม่เกิน budget (ทุก policy ยกเว้น B0)
- `opt_strict_recall >= strict_recall` ของทุก policy (ซ้ำจาก WP-07 แต่รันบนหลาย regime)
- `partial_recall >= strict_recall` เสมอ
- `competitive_ratio <= 1.0` เสมอ
- retention score ∈ [0,1]
- ไม่มี query ที่อ้าง item จากอนาคต

## 3. `tests/test_layering.py` — บังคับ scope guard (§12, §15 ข้อ 2)
- ไม่มีไดเรกทอรี `somaos/kernel|registry|cortex|modelbus|trace|packs` อยู่จริง
- `somaos/bench/trace/` ไม่ import `somaos.broker.policies` (WP-02 §5.3)
- `somaos/bench/report.py` ไม่ import `somaos.broker`
- `somaos/broker/retention.py` ไม่ import อะไรนอกจาก stdlib + `somaos.broker.types`
- ไม่มี third-party import นอก numpy (สแกน AST ทั้ง package)

## 4. CI (`.github/workflows/ci.yml` หรือสคริปต์ `scripts/ci.sh` ถ้ายังไม่มี remote)
```
ruff check .        (ถ้าติดตั้งได้ — ไม่งั้นข้าม ไม่ใช่ hard dep)
pytest -q -m "not perf"
pytest -q -m perf   (ไม่บล็อก CI, รายงานอย่างเดียว)
python -m somaos.bench.runner --config bench/configs/smoke.json
python -m somaos.bench.report --in runs/ --out runs/report.md
```
`bench/configs/smoke.json` = config จิ๋วที่รันจบใน < 60 วินาที

## Acceptance
- `pytest -q` เขียวทั้งหมด
- coverage: `retention.py` ≥ 95%, `workingset.py` ≥ 90%, `opt/oracle.py` ≥ 90%
- smoke run จบใน < 60 วินาที บนเครื่อง dev

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-10-determinism-ci.md`
> `test_layering.py` (§3) คือสิ่งที่กัน scope creep ไม่ให้เกิดขึ้นเงียบ ๆ — ทำให้ครบ
> repo ยังไม่ใช่ git repo: ถ้าจะตั้ง CI ให้ทำเป็น `scripts/ci.sh` ก่อน แล้วแจ้งผู้ใช้ว่าต้อง `git init` เอง
