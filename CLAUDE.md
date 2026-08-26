# CLAUDE.md — SomaOS

## อ่านก่อนเสมอ
1. `target_SomaOS.md` — north-star document (ผู้มีอำนาจสูงสุด ถ้าขัดกับไฟล์อื่น ให้ยึดไฟล์นี้)
2. `plans/00_PHASE0_MASTER_PLAN.md` — แผนงานปัจจุบัน
3. `plans/01_DECISIONS.md` — ข้อตัดสินใจที่ล็อกแล้ว (ห้ามเปลี่ยนเงียบ ๆ)
4. `plans/02_INTERFACES.md` — contract ที่ทุก work package ต้องเคารพ

## สถานะปัจจุบัน
`PHASE 0 — Memory Policy Spike` เท่านั้น
แตะได้เฉพาะ `somaos/broker/`, `somaos/bench/`, `tests/`
**ห้ามสร้าง** `kernel/`, `registry/`, `cortex/`, `modelbus/`, `trace/`, `packs/`

## กฎการทำงาน (สรุปจาก §15)
- ถ้าคำขอขัด §12 (non-goals) → ทักท้วงก่อนทำ
- ทุก policy implement `MemoryPolicy` protocol เดียวกัน สลับผ่าน config
- retention scoring = pure function → เขียน test ก่อน
- dependency: stdlib เป็นหลัก + numpy เท่านั้น (ห้ามเพิ่มโดยไม่ถาม)
- metric ทุกตัว export เป็น structured data (JSONL) ห้าม print
- ถ้า kill criteria §7.4 เป็นจริง → **รายงานตรง ๆ ห้ามแก้ตัวเลข**

## คำสั่งที่ใช้บ่อย
```bash
python -m somaos.bench.runner --config bench/configs/phase0.json
python -m somaos.bench.report --in runs/ --out runs/report.md
pytest -q
```
