# CLAUDE.md — SomaOS

## อ่านก่อนเสมอ
1. `target_SomaOS.md` — north-star document **v2** (ผู้มีอำนาจสูงสุด ถ้าขัดกับไฟล์อื่น ให้ยึดไฟล์นี้)
2. `plans/03_MEMORY_ARCHITECTURE.md` — สเปกโครงสร้างหน่วยความจำ (หัวใจของ Phase 0b)
3. `plans/01_DECISIONS.md` — ข้อตัดสินใจที่ล็อกแล้ว N-01..N-15 (ห้ามเปลี่ยนเงียบ ๆ)
4. `plan.md` — แผนงานและสถานะปัจจุบัน
5. `plans/ARCHIVE_PHASE0_RESULT.md` — ผลการวัดของดีไซน์เก่าที่ถูกยกเลิก (อ่านเพื่อไม่ทำผิดซ้ำ)

## สถานะปัจจุบัน
`PHASE 0b — Memory Structure Spike`
แตะได้เฉพาะ `somaos/broker/`, `somaos/bench/`, `tests/`
**ห้ามสร้าง** `kernel/`, `registry/`, `cortex/`, `modelbus/`, `trace/`, `packs/`

## กฎการทำงาน
- ถ้าคำขอขัด §12 (non-goals) → ทักท้วงก่อนทำ
- **ห้ามลบความทรงจำ** — บีบอัดได้ เจือจางได้ ลบไม่ได้ (N-01)
- **engine ตัดสินใจจากเวกเตอร์เท่านั้น** ข้อความเป็นเงาสำหรับมนุษย์ (N-02)
- **ห้ามมี code path ที่ O(N) ต่อการนึกหนึ่งครั้ง** (N-08)
- ทุก policy implement interface เดียวกัน สลับผ่าน config
- เขียน test ก่อนสำหรับ invariant ใน `03_MEMORY_ARCHITECTURE.md` §7
- dependency: stdlib + numpy เท่านั้น (ห้ามเพิ่มโดยไม่ถาม)
- metric ทุกตัว export เป็น structured data (JSONL) ห้าม print
- ถ้า kill criteria §7.4 เป็นจริง → **รายงานตรง ๆ ห้ามแก้ตัวเลข**
- push ทันทีที่จบแต่ละ WP อย่ารอสะสม

## คำสั่งที่ใช้บ่อย
```bash
python -m somaos.bench.runner --config somaos/bench/configs/smoke.json
python -m somaos.bench.report --in runs/ --out runs/report.md
pytest -q
```
