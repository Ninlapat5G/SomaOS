# Handoff protocol → Sonnet

## กติกาหนึ่งข้อ
**ส่งทีละ WP** ห้ามส่งพร้อมกันหลายตัว ห้ามข้าม dependency
ก่อนส่ง WP ถัดไป: acceptance test ของ WP ก่อนหน้าต้องเขียวจริง (ไม่ใช่ "น่าจะเขียว")

## Template ที่ใช้เปิดงานทุกครั้ง

```
Context ที่ต้องอ่านก่อน (ตามลำดับ):
1. target_SomaOS.md            — north-star ยึดไฟล์นี้เหนือทุกอย่าง
2. CLAUDE.md                   — scope guard ปัจจุบัน
3. plans/01_DECISIONS.md       — ข้อตัดสินใจที่ล็อกแล้ว ห้ามเปลี่ยนเงียบ
4. plans/02_INTERFACES.md      — contract ที่ต้องเคารพ
5. plans/wp/<WP ที่จะทำ>.md    — งานวันนี้

งาน: implement <WP-XX> ให้จบตาม acceptance criteria ทุกข้อ

กฎ:
- แตะได้เฉพาะไฟล์ที่ WP ระบุ + tests/ ที่เกี่ยวข้อง
- ห้ามสร้าง kernel/ registry/ cortex/ modelbus/ trace/ packs/
- ห้ามเพิ่ม dependency นอก stdlib + numpy
- ถ้า spec ขัดแย้งกันเอง หรือทำตามไม่ได้ → หยุด รายงาน อย่าเดา
- ห้ามแก้ threshold/weight/regime config เพื่อให้ test หรือ gate ผ่าน
- จบด้วย: รัน pytest จริง แล้วรายงานผลตามจริง (รวมข้อที่ fail)
```

## ลำดับ
```
WP-00 → WP-01 → (WP-02 ‖ WP-03 ‖ WP-04) → WP-05 → WP-06 → WP-07 → WP-08 → WP-09 → WP-10
```
**Checkpoint บังคับให้คนตรวจ (Nin):**
- หลัง WP-02 — ตรวจ `bench/configs/regimes.json` แล้ว freeze ก่อนอนุญาต WP-06
- หลัง WP-07 — ตรวจว่า `opt_strict_recall >= policy` ทุกเคส (ถ้าไม่ใช่ = มี leak)
- หลัง WP-09 — อ่านตัวเลข gate จริงแล้วตัดสิน Phase 0 เอง **ห้าม Sonnet ตัดสิน**

## สัญญาณเตือนที่ต้องหยุดทันที
- policy ไหนก็ตามได้ `competitive_ratio > 1.0`
- B0 ได้ `strict_recall < 1.0`
- S ชนะ B2 ใน regime `adversarial_flat`
- test ผ่านหลังจากที่มีการแก้ config/threshold ในคอมมิตเดียวกัน

ทั้งสี่ข้อแปลว่า **harness ผิด** ไม่ใช่ **policy เก่ง**
