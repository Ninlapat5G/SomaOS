# WP-06 — Policy S (SomaOS policy-driven working set) ★

**Depends on:** WP-03, WP-04, WP-05  **Blocks:** WP-08  **Est:** 1 d
**ห้ามเริ่มก่อน `bench/configs/regimes.json` ถูก commit (WP-02 §5.2)**

## Goal
`somaos/broker/policies/s_soma.py` — ประกอบ retention engine + working set allocator
เป็น policy ที่ implement interface เดียวกับ B0–B4

## พฤติกรรม
```
observe(obs):
    surprise gating (§5.2):
      surprise >= tau_high หรือ novelty == 1.0  → encode เต็ม (เก็บ item)
      มิฉะนั้น → ไม่เก็บ item ใหม่ แต่ +1 ให้ observation_count ของ item ที่ใกล้ที่สุด
                 (nearest = sim สูงสุดใน (topics, entities) และ ≥ merge_threshold)
                 คืน EncodeDecision(encoded=False, reason="low_surprise_counter", counter_delta=1)
    → ตรงนี้คือกลไกที่ทำให้ "จำระยะยาวได้โดยไม่บวม"

on_tick(tick):
    ทุก realloc_every ticks → allocator.allocate(...) ด้วย goal ปัจจุบัน
    (goal ที่ L1 = topic/entity ที่ปรากฏถี่ที่สุดใน W ticks ล่าสุด — ไม่ใช้ query ล่วงหน้า!)

on_query(q):
    1. เริ่มจาก working set ปัจจุบัน
    2. targeted recall: เติม item จาก WARM ที่ relevance ต่อ q สูง จนเต็ม budget
    3. เรียง static-before-dynamic: pinned/semantic ก่อน แล้ว episodic เรียงตาม tick
    4. คืน ContextBundle
```

## กฎเหล็กที่ห้ามละเมิด (ไม่งั้นผลไม่มีความหมาย)
1. **ห้าม `on_tick`/`observe` มองเห็น query ในอนาคต** — มีเฉพาะ OPT ที่ทำได้
   ต้องมี test ที่ยืนยันว่า S ไม่ import/อ่าน `trace.events` ทั้งก้อน
2. **ห้ามอ่าน `Query.required_item_ids` ใน on_query** — นั่นคือเฉลย
   ป้องกันด้วย: runner ส่ง `Query` เวอร์ชันที่ `required_item_ids` ถูกปิดให้ policy
   (ดู WP-08 §3 — `QueryView` ที่ไม่มีฟิลด์เฉลย) ← **ทำเป็น structural guarantee ไม่ใช่ convention**
3. เวลาที่ใช้ใน `observe` + `on_tick` ต้องอยู่ใน budget D-07

## Acceptance (tests/test_policy_s.py)
1. ผ่าน conformance suite ของ WP-05 ทุกข้อ
2. leak test: `on_query` ที่รับ `QueryView` ต้องทำงานได้ (ไม่มี AttributeError จากการพยายามอ่านเฉลย)
3. `encode_rate` ใน regime `high_noise` ต้อง < 0.35 (surprise gating ทำงานจริง)
4. `encode_rate` ใน regime `adversarial_flat` ต้องใกล้ค่า baseline (gating ไม่ช่วยและไม่พัง)
5. determinism: seed เดิม → sequence ของ `bundle_hash` เท่ากัน bit-for-bit

## Prompt สำหรับ Sonnet
> implement `plans/wp/WP-06-policy-s.md`
> อ่าน §5.2 และ §5.4 ของ `target_SomaOS.md` ก่อน — S คือ fast path ล้วน ห้ามแตะ LLM ห้ามทำ consolidation
> (consolidation = slow path เป็นของ Phase 5 ไม่ใช่ตอนนี้)
> กฎเหล็กข้อ 2 ต้องเป็น structural guarantee — ถ้า implement เป็นแค่ "ระวังไม่อ่าน" ถือว่าไม่ผ่าน
