# Phase 0 — Master Plan (Memory Policy Spike)

> Derived from `target_SomaOS.md` §7, §11, §12, §15
> Scope: `somaos/broker/` + `somaos/bench/` เท่านั้น
> เป้าเวลา: 2 สัปดาห์ | ผู้ execute: Sonnet | ผู้ตรวจ gate: Nin

---

## 0. คำถามเดียวที่ Phase 0 ต้องตอบ

> ที่ token budget เท่ากัน — policy `S` ให้ `recall_accuracy` สูงกว่า `B2` (naive RAG)
> อย่างมีนัยสำคัญไหม และห่างจาก `OPT` แค่ไหน (`competitive_ratio ≥ 0.7`)

ทุกอย่างที่ไม่ได้รับใช้คำถามนี้ = out of scope ของ Phase 0

---

## 1. โครงที่จะได้ตอนจบ

```
somaos/
├── broker/
│   ├── types.py            # WP-01  data contract
│   ├── policy.py           # WP-05  MemoryPolicy protocol + registry
│   ├── retention.py        # WP-03  ★ retention scoring engine (pure)
│   ├── workingset.py       # WP-04  ★ working set allocator
│   ├── policies/
│   │   ├── b0_full.py      # WP-05
│   │   ├── b1_window.py    # WP-05
│   │   ├── b2_rag.py       # WP-05
│   │   ├── b3_summarize.py # WP-05
│   │   ├── b4_paging.py    # WP-05  (cost-model proxy — ดู §3.4)
│   │   └── s_soma.py       # WP-06  ★ ของเรา
│   └── opt/
│       └── oracle.py       # WP-07  ★ OPT harness
├── bench/
│   ├── trace/
│   │   ├── world.py        # WP-02  ground-truth world model
│   │   └── generator.py    # WP-02  synthetic trace generator
│   ├── metrics.py          # WP-08
│   ├── runner.py           # WP-08
│   ├── report.py           # WP-09
│   ├── gate.py             # WP-09  kill-criteria checker
│   └── configs/phase0.json # WP-08
└── tests/                  # WP-10 + test ประจำ WP
```

★ = 3 ชิ้นที่ §7.1 อนุญาตให้สร้าง ที่เหลือคือ scaffolding ที่จำเป็นต่อการวัดผล

---

## 2. Work packages & dependency graph

```
WP-00 scaffold
   └─> WP-01 types ─┬─> WP-02 trace generator ─┐
                    ├─> WP-03 retention  ──────┤
                    ├─> WP-04 workingset ──────┤
                    └─> WP-05 policy iface ────┤
                              │                │
                              ├─> WP-06 policy S (needs 03+04)
                              └─> WP-07 OPT oracle (needs 02)
                                        │
                                        └─> WP-08 metrics+runner
                                                  └─> WP-09 report+gate
                                                            └─> WP-10 determinism/CI
```

| WP | ชื่อ | ประมาณเวลา | ขนานได้กับ |
|---|---|---|---|
| WP-00 | Repo scaffold & conventions | 0.5 d | — |
| WP-01 | Data contracts (Phase 0 subset) | 0.5 d | — |
| WP-02 | Synthetic trace generator + world | 2 d | WP-03/04/05 |
| WP-03 | Retention scoring engine | 1.5 d | WP-02, WP-05 |
| WP-04 | Working set allocator | 1.5 d | WP-02 |
| WP-05 | Policy interface + B0–B4 | 2 d | WP-03/04 |
| WP-06 | Policy S | 1 d | — |
| WP-07 | OPT oracle harness | 2 d | WP-05/06 |
| WP-08 | Metrics + runner | 1 d | — |
| WP-09 | Report + kill-criteria gate | 1 d | — |
| WP-10 | Determinism & property tests | 1 d | — |

**ลำดับส่งงานให้ Sonnet ที่แนะนำ:** 00 → 01 → (02 ‖ 03 ‖ 04) → 05 → 06 → 07 → 08 → 09 → 10
ห้ามเริ่ม WP-N ก่อน acceptance test ของ dependency ผ่าน

---

## 3. ความเสี่ยงเชิงเทคนิคที่ Sonnet ต้องรู้ล่วงหน้า

### 3.1 OPT ที่ item ขนาดไม่เท่ากันเป็น NP-hard
Belady MIN optimal เฉพาะกรณี **item ขนาดเท่ากัน** พอ item มี `tokens` ต่างกัน ปัญหากลายเป็น
generalized caching → NP-hard **ห้ามเรียกผลลัพธ์ว่า OPT เฉย ๆ**
วิธีที่ใช้: ดู `plans/wp/WP-07-opt-oracle.md` (exact mode + upper-bound mode)
`competitive_ratio` ต้องคำนวณเทียบ **upper bound** เสมอ → ตัวเลขที่ได้เป็น conservative (ต่ำกว่าความจริง) ซึ่งปลอดภัยกว่า

### 3.2 การวัดตัวเองในสุญญากาศ
L1 คือโลกที่เราสร้างเอง → generator ต้องไม่ถูกออกแบบให้ policy `S` ชนะโดยบังเอิญ
มาตรการบังคับ: `plans/wp/WP-02-trace-generator.md` §5 (adversarial regimes + pre-registration ของ regime ก่อนรัน S)

### 3.3 Kill criteria ต้องเป็นโค้ด ไม่ใช่วิจารณญาณ
`bench/gate.py` ต้อง return PASS/FAIL เอง และ report ต้องพิมพ์ผลนั้นเป็นบรรทัดแรก

### 3.4 B4 ที่ L1 ไม่ใช่ MemGPT จริง
Phase 0 ไม่มี LLM → B4 เป็น **cost-model proxy** (heuristic paging + คิดค่า llm_call ต่อการตัดสินใจ page)
ต้องเขียนคำเตือนนี้ใน report ทุกครั้ง การเทียบกับ MemGPT/Letta ของจริงเกิดที่ Phase 0.5 / L2

### 3.5 Fast-path cost budget (kill criterion ข้อ 3)
"เกิน 5% ของ compute รวม" ที่ L1 ไม่มี LLM ให้เทียบ → ใช้ reference cost model ที่ล็อกไว้ใน `01_DECISIONS.md` D-07

---

## 4. Definition of Done ของทั้ง Phase 0

- [ ] `pytest -q` ผ่านทั้งหมด, coverage ของ `retention.py` ≥ 95%
- [ ] `python -m somaos.bench.runner --config bench/configs/phase0.json` รันจบ ได้ `runs/*.jsonl`
- [ ] รันซ้ำด้วย config เดิม → JSONL identical bit-for-bit (ยกเว้นฟิลด์ timing ที่แยกไฟล์)
- [ ] `python -m somaos.bench.report` ออก `report.md` + `report.json` ที่มี:
      บรรทัดแรก = `PHASE0 GATE: PASS|FAIL` พร้อมเหตุผลรายข้อของ §7.4
- [ ] ตาราง policy × budget × seed พร้อม CI (bootstrap) ของ `recall_accuracy` และ `tokens_per_query`
- [ ] `competitive_ratio` ของ S รายงานพร้อมระบุว่าเทียบ OPT-exact หรือ OPT-upper-bound
- [ ] ไม่มี dependency นอก stdlib + numpy
- [ ] ไม่มีไฟล์ใดถูกสร้างนอก `somaos/broker/`, `somaos/bench/`, `tests/`

---

## 5. Gate ออกจาก Phase 0 (ตัดสินโดย Nin ไม่ใช่ Sonnet)

ผ่าน → Phase 0.5 (LoCoMo / LongMemEval, ดู `plans/05_PHASE1_PLUS_OUTLINE.md`)
ไม่ผ่าน → หยุด ตาม §7.4 "เสียเวลาสองสัปดาห์ ดีกว่าเสียหกเดือน"

**ห้าม Sonnet ตัดสิน gate เอง ห้ามปรับ weight/config เพื่อให้ผ่าน**
ถ้าตัวเลขไม่ผ่าน → รายงานตัวเลขนั้น พร้อม hypothesis ว่าทำไม แล้วหยุด
