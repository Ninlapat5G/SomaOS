# target_SomaOS.md

> **North-star document** สำหรับโปรเจกต์ SomaOS
> เอกสารนี้ตั้งใจให้ใช้เป็น context หลักใน Claude Code — อ่านก่อนเขียนโค้ดทุกครั้ง
> Status: `PRE-BUILD / RESEARCH SPIKE` — ยังไม่อนุมัติให้สร้าง full kernel
> Owner: Nin (Phatchradanai)
> Last updated: 2026-08-26

---

## 0. TL;DR

SomaOS คือ **memory management runtime สำหรับ LLM agent** ที่ปฏิบัติกับ context window เหมือน physical RAM และปฏิบัติกับ external store เหมือน disk

หลักการเดียวที่ต้องจำ:

> **Engine เป็นคนถือโลก โมเดลเป็นคนถือความฉลาด**
> **Context คือทรัพยากรที่ OS จัดสรร ไม่ใช่ที่ที่โลกอาศัยอยู่**

สิ่งที่ต่างจาก agent memory framework ที่มีอยู่: **policy เป็นคนตัดสินใจเรื่อง memory ไม่ใช่ LLM** → deterministic, replay ได้, ถูกกว่า, และ benchmark เทียบ oracle ได้

---

## 1. เป้าหมายของเจ้าของโปรเจกต์

### 1.1 เป้าหมายเชิงเทคนิค

- สร้าง **OS ที่จัดการ memory ให้ AI agent ในแบบที่คล้ายสมองมนุษย์** — รู้เองว่าอะไรควรอยู่ใน short-term, อะไรควรถูกกลั่นเป็นความรู้ระยะยาว, อะไรควรทิ้ง
- ทำให้เป็น **domain-agnostic** — kernel เดียว ใช้ได้หลายโดเมนผ่าน "pack"
- ไปให้ถึงระดับ **production-grade** ไม่ใช่ research prototype: มี determinism, replay, observability, failure mode ที่ชัด, cost governance
- ใช้ศาสตร์ **Computer Science + Computer Engineering** เป็นฐาน (OS theory, DB theory, distributed systems) ไม่ใช่ prompt engineering

### 1.2 เป้าหมายเชิงยุทธศาสตร์

- เป็น **backbone ที่ใช้ซ้ำได้** ข้ามโปรเจกต์ของตัวเอง — SynaptaOS (smart home), thesis (AI Agent สัมภาษณ์งาน / HR), และงาน BCI/gaming ในอนาคต
- เป็นสินทรัพย์ทางเทคนิคที่ต่อยอดเป็นธุรกิจได้ (สอดคล้องกับ roadmap IoT/AI consulting)
- เป็นงานที่มี **defensible differentiation** ไม่ใช่ wrapper บาง ๆ

### 1.3 สิ่งที่ *ไม่ใช่* เป้าหมาย

- ไม่ได้จะทำ RPG engine
- ไม่ได้จะทำ RAG library อีกตัว
- ไม่ได้จะพิสูจน์ว่าแนวคิดนี้ใหม่ (มันไม่ใหม่ และเรารู้)
- ไม่ได้จะสร้าง general framework ก่อนมี use case จริง

---

## 2. Thesis ของโปรเจกต์

**ข้อเสนอที่ต้องพิสูจน์:**

> ถ้าจัดการ memory ด้วย **policy แบบ deterministic** (แทนที่จะให้ LLM ตัดสินใจเอง)
> agent จะรักษาคุณภาพระยะยาวได้ **เท่าหรือดีกว่า** baseline
> ที่ **ต้นทุนต่ำกว่าอย่างมีนัยสำคัญ**
> และ **อธิบายได้ว่าความจำชิ้นไหนทำให้เกิดการตัดสินใจไหน**

ถ้าข้อนี้ผิด — โปรเจกต์นี้ไม่ควรถูกสร้าง

---

## 3. ที่มาและตำแหน่งเทียบกับงานที่มีอยู่

### 3.1 Prior art ที่ต้องอ่านก่อนเขียนโค้ด

| งาน | สิ่งที่เอามาใช้ |
|---|---|
| Denning, Working Set Model (1968) | นิยาม short-term memory เชิงรูปนัย + ทฤษฎี thrashing |
| Belady's OPT / MIN | oracle baseline สำหรับวัด policy |
| Event Sourcing + CQRS | event log เป็น source of truth, state เป็น projection |
| ECS (Entity Component System) | entity model ที่ถอดประกอบได้ |
| BDI — Rao & Georgeff (1995) | belief–desire–intention loop |
| Utility AI (The Sims) | scored candidate selection |
| AGM belief revision | semantics ของการแก้ belief |
| Generative Agents — Park et al. (2023) | memory stream + reflection + planning |
| MemGPT / Letta (2023) | virtual memory metaphor สำหรับ LLM |
| Mem0 / Zep / LangMem | extraction + temporal knowledge graph |
| LoCoMo, LongMemEval | benchmark ความจำระยะยาว |

> ⚠️ **หมายเหตุ:** วงการนี้ขยับเร็วมาก รายการข้างบนสะท้อนความรู้ ณ ต้นปี 2026
> ก่อนลงทุนหนัก ต้อง survey state ปัจจุบันซ้ำ — โดยเฉพาะ MemGPT/Letta และ benchmark ใหม่

### 3.2 ช่องว่างที่ SomaOS จะปักธง

| # | ช่องว่าง | ทำไมของเดิมทำไม่ได้ | ระดับ moat |
|---|---|---|---|
| 1 | Policy-driven memory (ไม่ใช้ LLM ตัดสิน) | ของเดิมให้ LLM page ตัวเอง → แพง + nondeterministic | สูง |
| 2 | Tick loop | ของเดิมเป็น request/response → ไม่มีเวลาเป็นแกน | สูง |
| 3 | **Prospective memory** | ต้องมี tick + event bus ถึงทำได้ | **สูงสุด** |
| 4 | **Surprise-gated encoding** (memory ผูกกับ belief revision เป็นกลไกเดียว) | ของเดิมแยก memory กับ reasoning ออกจากกัน | **สูงสุด** |
| 5 | **Causal trace: memory → decision** | ของเดิมไม่มี state lineage | **สูงสุด** |
| 6 | Benchmark เทียบ OPT oracle | แทบไม่มีใครทำ | สูง |

**ข้อ 3, 4, 5 คือ moat ตัวจริง** ที่เหลือคือ table stakes

---

## 4. สถาปัตยกรรม

### 4.1 Mapping หลัก

| OS concept | SomaOS |
|---|---|
| Physical RAM | context window (fixed, แพง) |
| Disk | event log + semantic store |
| Page fault | retrieval miss |
| Page replacement policy | retention policy |
| Working set `W(t, τ)` | short-term memory |
| Thrashing | context churn จน task ไม่คืบ |
| Process | agent |
| Scheduler quantum | tick |
| Device driver / HAL | model bus |

### 4.2 Layer stack

```
┌─────────────────────────────────────────────────────────┐
│  L6  SOMA PACKS        userland: domain-specific         │
│      social-sim · hr-interview · smart-home · game-npc   │
├─────────────────────────────────────────────────────────┤
│  L5  SOMA TRACE        causal lineage · replay · XAI     │
├─────────────────────────────────────────────────────────┤
│  L4  SOMA MODEL BUS    HAL for LLM · contract · fallback │
│  ═════════ SYSCALL BOUNDARY (proposal only) ═══════════  │
├─────────────────────────────────────────────────────────┤
│  L3  SOMA BROKER  ★    context MMU · budget allocator    │
│                        retention policy · consolidation  │
├─────────────────────────────────────────────────────────┤
│  L2  SOMA CORTEX       perceive→belief→candidate→        │
│                        score→decide→effect               │
├─────────────────────────────────────────────────────────┤
│  L1  SOMA REGISTRY     entity/component · schema version │
├─────────────────────────────────────────────────────────┤
│  L0  SOMA KERNEL       event log · tick sched · txn ·    │
│                        seeded RNG · snapshot/compaction  │
└─────────────────────────────────────────────────────────┘

★ = ชั้นที่เป็นหัวใจของโปรเจกต์นี้ ที่เหลือมีไว้รับใช้มัน
```

### 4.3 กฎเหล็กของ kernel

1. **LLM ไม่เคยเขียน state โดยตรง** — คืนได้แค่ proposal, kernel ตรวจ schema + invariant แล้วค่อย apply เป็น event
   → แก้ prompt injection, determinism, และ blast radius ตอนโมเดลหลอน พร้อมกัน
2. **Event log คือ source of truth เดียว** — state store ทุกตัวเป็น materialized projection
3. **Summary คือ cache ไม่ใช่ต้นฉบับ** — compression ต้องเก็บ pointer กลับไปหา raw event เสมอ
4. **RNG ต้อง seeded แยก stream ต่อ entity** — ไม่งั้น replay พัง
5. **บันทึก model output ลง event log** — replay ใช้ของที่บันทึกไว้ (VCR pattern) → ระบบที่มี component nondeterministic กลับมา deterministic 100%
6. **Tick loop ห้าม block รอ LLM** — decision มี deadline, เกิน budget ให้ reflex policy ยิงก่อน, ผลที่มาทีหลังเข้าเป็น revision event (correlation ID + idempotent apply)
7. **ไม่มี `write()` API สำหรับ memory** — ทุกอย่างเข้าผ่าน `perceive()` แล้วให้ policy ตัดสิน

---

## 5. Memory model — ส่วนที่เป็นหัวใจ

### 5.1 ประเภทของ memory (ห้ามเหมารวมเป็น vector store ก้อนเดียว)

```
Working     → context window          ชั่วคราว จัดสรรใหม่ทุก tick
Episodic    → event log               เกิดอะไร เมื่อไหร่ กับใคร (append-only)
Semantic    → knowledge graph         ข้อเท็จจริงที่กลั่นแล้ว
Procedural  → cached policy/skill     เคยทำแบบนี้แล้วได้ผล
Prospective → trigger registry        ตั้งใจจะทำ X เมื่อ Y เกิด
```

**Prospective memory = interrupt handler ที่ลงทะเบียนกับ event bus** ไม่ใช่ retrieval

### 5.2 Surprise-gated encoding

หลักการจากสมอง: **สิ่งที่ทำนายถูกอยู่แล้วแทบไม่ถูกบันทึก สิ่งที่ขัดความคาดหมายถูกบันทึกแรง**

```
surprise(obs) = divergence( predicted_state, observed_state )
              ≈ 1 - confidence( belief ที่ทำนาย obs นี้ )
```

| surprise | การกระทำ |
|---|---|
| ต่ำ | เพิ่ม `confidence` + `observation_count` เท่านั้น — **ไม่เก็บ episode** |
| สูง | เก็บ episode เต็ม + trigger belief revision + promote เข้า working set |

นี่คือกลไกที่ทำให้ระบบ **จำระยะยาวได้โดยไม่บวม** เพราะ ~90% ของสิ่งที่เกิดขึ้นคือการยืนยันสิ่งที่รู้อยู่แล้ว → ควรถูก compress เป็น counter ไม่ใช่ record

**ผลพลอยได้สำคัญ:** memory layer กับ belief layer กลายเป็นกลไกเดียวกัน ไม่ใช่สองระบบที่ต้อง sync กัน

### 5.3 Retention score

```
retention = w₁·recency_decay
          + w₂·access_frequency
          + w₃·semantic_relevance(current_goal)
          + w₄·surprise
          + w₅·pinned
          + w₆·recompute_cost
```

- **weight เป็นของ pack ไม่ใช่ของ kernel**
  - social-sim / hr-interview → `w₄` (surprise) สูง
  - smart-home → `w₂` (frequency) สูง (เปิดไฟห้องนอนทุกคืน = ไม่ surprise แต่สำคัญมาก)
- kernel ให้แค่กลไก + interface

### 5.4 Two-speed architecture

**Fast path — ทุก tick, ไม่แตะ LLM**
- คำนวณ retention score ด้วย heuristic ล้วน
- จัดสรร context budget
- promote/demote ระหว่าง tier
- ต้อง deterministic, วัดเป็นไมโครวินาที

**Slow path — Consolidation Cycle (offline, แตะ LLM ได้)**
- replay event log ในหน้าต่างเวลา
- ยุบ episodic ซ้ำ ๆ เป็น semantic
- หา contradiction แล้ว resolve
- เขียน summary ใหม่ + invalidate summary ที่ stale
- demote/evict ตาม policy

> นี่คือ "การนอน" ของระบบ และเป็นจุดที่ LLM คุ้มที่สุด — เรียกครั้งเดียวต่อหลายพัน event

### 5.5 Working set & thrashing

จาก Denning: ถ้า `token_budget < working_set(task)` ระบบจะ thrash — คุณภาพพังแบบ **non-linear** ไม่ใช่ค่อย ๆ แย่ลง

ต้องวัดด้วย:
```
context_churn_rate  = จำนวน item ที่เข้า-ออก working set ต่อ tick
task_progress_rate  = ความคืบหน้าต่อ tick
thrash_indicator    = churn สูง + progress ต่ำ
```

### 5.6 สิ่งที่ห้ามลอกจากสมอง

| ห้ามลอก | เหตุผล | ทำแทนด้วย |
|---|---|---|
| Reconstructive recall ที่ทำลายของเดิม | สมองแต่งเติมทุกครั้งที่นึก และของเดิมหายถาวร | summary เก็บ pointer กลับ raw event เสมอ |
| การลืมแบบ decay เฉย ๆ | ไม่มี audit trail | eviction ที่มีคะแนน + log ว่าลืมอะไรเพราะอะไร |
| ให้ neuroscience กำหนด guarantee | ไม่ให้ correctness bound / complexity bound | เอา guarantee มาจาก OS/DB theory |

**สรุป:** metaphor สมองใช้สร้างไอเดีย (surprise gating, consolidation, working set) — ส่วน guarantee มาจาก CS

---

## 6. Syscall interface (ร่าง)

```python
soma.perceive(obs) -> EncodeDecision      # fast path, surprise-gated
soma.recall(query, budget, mode) -> ContextBundle
soma.pin(mem_id, ttl)                     # บังคับให้อยู่ใน working set
soma.intend(trigger, action)              # prospective memory
soma.consolidate(window)                  # slow path, LLM eligible
soma.forget(policy) -> EvictionLog        # active + audited
soma.explain(decision_id) -> Lineage      # memory ไหน → decision ไหน
```

**ไม่มี `soma.write()` โดยเจตนา**

### Data contract หลัก

```python
Event         { id, tick, actor, type, payload, causation_id, correlation_id }
Belief        { claim, confidence, provenance[], first_seen, last_seen,
                observation_count, decay_fn, contradicts[] }
MemoryItem    { id, kind, content, embedding?, retention_score,
                tier, raw_event_refs[], created_tick, last_access_tick }
ContextBundle { bundle_hash, budget, items[], layout_zones, cache_key }
Decision      { id, tick, agent, candidates[], scores{}, chosen,
                bundle_hash, model_call_id?, lineage[] }
```

**ContextBundle ต้อง hashable + deterministic** เพื่อทำ cache key และ replay
**Layout ต้องบังคับ static-before-dynamic ที่ระดับ API** เพื่อรักษา prefix cache — เป็นกฎของ broker ไม่ใช่ best practice ที่ pack author ต้องจำเอง

---

## 7. Research spike — สิ่งที่จะวิจัยเพื่อทดสอบแนวคิด

> **ห้ามสร้าง kernel เต็มก่อนผ่าน Phase 0**
> Phase 0 ตอบคำถามที่แพงที่สุดตั้งแต่ต้น: policy ของเราดีกว่า baseline จริงไหม และดีกว่าเท่าไหร่เมื่อเทียบ oracle

### 7.1 Phase 0 — Memory Policy Spike (เป้า: 2 สัปดาห์)

สร้างแค่ **3 ชิ้น** ไม่มากกว่านี้:

1. **Retention scoring engine** — pure function, ไม่แตะ LLM
2. **Working set allocator** — จัดสรร token budget, promote/demote, วัด churn
3. **OPT harness** — replay session ที่จบแล้วย้อนหลัง คำนวณว่า "ถ้ารู้อนาคต ควรเก็บอะไร" → oracle baseline

**ลำดับการทำ:**
- Step 1: สร้าง synthetic long-horizon trace **โดยไม่มี LLM เลย** (deterministic, ทดสอบเร็ว, ฟรี)
- Step 2: รัน policy ทุกตัวบน trace เดียวกัน วัด competitive ratio เทียบ OPT
- Step 3: ถ้าตัวเลขน่าสนใจ → ค่อยเอา LLM เข้า loop ในสเกลเล็ก

### 7.2 Baseline ที่ต้องเอาชนะ

| id | policy | บทบาท |
|---|---|---|
| B0 | full context (ยัดทุกอย่าง) | upper bound คุณภาพ / upper bound cost |
| B1 | sliding window last-K | baseline ที่ง่ายที่สุด |
| B2 | naive RAG top-k vector | baseline ที่คนใช้จริง |
| B3 | summarize every N turns | baseline ที่แพร่หลาย |
| B4 | LLM-managed paging (MemGPT-style) | คู่แข่งตัวจริง |
| **S** | **SomaOS policy-driven working set** | ของเรา |
| OPT | oracle (รู้อนาคต) | upper bound เชิงทฤษฎี |

**เกณฑ์ที่ต้องการ:** `S` ต้องอยู่ใกล้ `B0` ด้านคุณภาพ ที่ต้นทุนใกล้ `B1` และต้องชนะ `B2`/`B3` ชัดเจน ส่วน `B4` ต้องชนะด้านต้นทุนและ determinism แม้คุณภาพเสมอกัน

### 7.3 Metrics

**Cost**
- `tokens_per_agent_day`
- `llm_call_ratio` = decision ที่แตะ LLM / decision ทั้งหมด
- `cost_per_1k_sim_events`

**Quality**
- `recall_accuracy` เทียบ ground truth (เรา generate โลกเอง จึงมี ground truth 100%)
- `persona_consistency_score`
- `contradiction_rate` — agent พูดขัดกับสิ่งที่ตัวเองเคยพูด

**Memory**
- `hit@k` ของ recall
- `context_churn_rate`
- **`competitive_ratio` = quality(S) / quality(OPT) ที่ budget เท่ากัน** ← ตัวชี้ขาด

**Explainability**
- `lineage_completeness` — % ของ decision ที่ trace กลับไปหา memory ต้นเหตุได้ครบ

### 7.4 Kill criteria — เงื่อนไขที่ต้องหยุดโปรเจกต์

หยุดทันทีถ้า **ข้อใดข้อหนึ่ง** เป็นจริงหลัง Phase 0:

- `S` ไม่ชนะ `B2` (naive RAG) อย่างมีนัยสำคัญที่ budget เท่ากัน
- `competitive_ratio < 0.7` — ห่างจาก oracle เกินไปจนไม่มีอะไรน่าเชื่อ
- ต้นทุนของ fast path เอง (คำนวณ retention ทุก tick) กินเกิน 5% ของ compute รวม
- surprise signal ไม่ correlate กับความสำคัญจริง (วัดจาก ground truth) → สมมติฐานหลักผิด

> เสียเวลาสองสัปดาห์ ดีกว่าเสียหกเดือน

---

## 8. Pack แรก: Social Media Simulation (`soma-pack-social`)

### 8.1 ทำไมโดเมนนี้ ไม่ใช่ game NPC

| เหตุผล | รายละเอียด |
|---|---|
| **Cost ต่ำสุด** | text-native ล้วน ไม่มี rendering, physics, game engine, asset |
| **เห็นภาพสุด** | emergent social phenomena เป็น demo ที่คนเข้าใจทันที |
| **มี ground truth 100%** | เรา generate โลกเอง → วัด belief divergence เชิงปริมาณได้ |
| **บีบทุก feature ของ SomaOS** | ดูตาราง 8.3 |
| **สเกลได้ทันที** | เพิ่ม agent จาก 10 → 200 โดยไม่ต้องแตะอย่างอื่น |

### 8.2 โครงโลกจำลอง

```
World      : feed timeline, topic space, ground-truth event stream
Agent      : persona + belief graph + social graph + memory store
Action     : scroll · react · comment · post · share · DM · mute · ignore
Tick       : 1 tick = 1 ช่วงเวลาในวัน (เช้า/กลางวัน/เย็น/ดึก)
Tier       : FOCUS (แตะ LLM ได้) / AMBIENT (symbolic) / DORMANT (aggregate)
```

**การควบคุมต้นทุน (สำคัญมาก):** agent ส่วนใหญ่ในแต่ละ tick แค่ scroll/react → **symbolic path ล้วน ไม่แตะ LLM** มีเพียงส่วนน้อยที่ comment/post ถึงเข้าสู่ LLM path
→ นี่คือสิ่งที่ทำลาย O(N) จริง ไม่ใช่แค่ context สั้นลง

### 8.3 Feature ของ SomaOS ถูกทดสอบยังไง

| Claim ของ SomaOS | ทดสอบด้วยอะไรใน social sim |
|---|---|
| Working set / OPT | agent ต้อง recall ประวัติที่ถูกต้องเพื่อคอมเมนต์ให้ตรงคาแรกเตอร์ |
| Surprise-gated encoding | อัตราส่วน event ที่ถูกเข้ารหัส vs ทั้งหมด เทียบคุณภาพกับ full-encode |
| Belief ≠ world state | วัด divergence ระหว่าง belief graph ของ agent กับ ground truth |
| Prospective memory | agent ทำตามที่ประกาศไว้จริงไหม ("เดี๋ยวจะไปเถียงต่อ") |
| Causal trace | ตอบได้ไหมว่า "ทำไม A ถึงตอบ B แบบมีอารมณ์" |
| Cost sublinearity | `tokens_per_agent_day` ตอน N = 10 → 50 → 200 |
| Consolidation | หลัง sleep cycle agent สรุป "คนนี้เป็นคนแบบไหน" ได้ถูกไหม |

### 8.4 Emergent phenomena ที่จะวัด (เป็น demo ด้วย)

- **Rumor propagation & telephone-game distortion** — ปล่อยข้อเท็จจริง 1 ชิ้น วัดว่ามันเพี้ยนไปตามระยะทางในกราฟสังคมยังไง (นี่คือ demo ที่ทรงพลังที่สุด เพราะแสดง belief divergence ให้เห็นเป็นภาพ)
- **Opinion clustering / polarization** — เกิด community โดยไม่ได้ hard-code
- **Parasocial asymmetry** — A จำ B ได้ละเอียด แต่ B แทบไม่รู้จัก A
- **Memory decay ในความสัมพันธ์** — คนที่ไม่ปฏิสัมพันธ์นาน retention score ตกยังไง

### 8.5 ข้อควรระวังเชิงจริยธรรม

- เป็น **simulation ล้วน** — ไม่แตะ Facebook API จริง, ไม่ดึงข้อมูลผู้ใช้จริง, ไม่สร้างบัญชีจริง
- persona ทั้งหมด synthetic ไม่อ้างอิงบุคคลจริง
- ถ้าจะเผยแพร่ผลงาน ต้องระบุชัดว่าเป็นโลกจำลอง เพื่อไม่ให้ถูกตีความว่าเป็นเครื่องมือ manipulation
- **ห้าม** พัฒนาต่อไปทาง astroturfing / bot network สำหรับ platform จริง

### 8.6 Validation Ladder

> **ปัญหาที่ต้องแก้:** ถ้าทดสอบแต่ในโลกที่เราสร้างเอง ระบบจะ validate ตัวเองในสุญญากาศ
> **แต่:** metric หลัก (`competitive_ratio` vs OPT, belief divergence, `recall_accuracy`) **ทั้งหมดต้องการ ground truth ที่เราควบคุม**
> → live public platform ให้ realism แต่พรากสิ่งที่เรากำลังวัดไป

**ทางออก: ไต่บันไดที่เพิ่ม realism ทีละขั้นโดยไม่ยอมเสีย ground truth**

```
L1  synthetic trace, no LLM        → competitive ratio vs OPT
L2  LoCoMo / LongMemEval           → เทียบคู่แข่งด้วยตัวเลขมาตรฐาน
L3  replay corpus จริง             → ทนต่อ distribution จริงไหม
L4  Mastodon instance ของตัวเอง    → API จริง protocol จริง คุม ground truth ได้
L5  human evaluation               → ความสมจริงเชิงคุณภาพ
──────────────────────────────────────────────────────────
    live public platform           → ไม่อยู่ใน roadmap (ดู 8.6.6)
```

#### 8.6.1 L1 — Synthetic trace (Phase 0)

- ไม่มี LLM เลย deterministic รันเร็ว ฟรี
- ใช้คำนวณ OPT ได้เพราะ trace จบแล้วและรู้อนาคตทั้งหมด
- **นี่คือขั้นเดียวที่คำนวณ `competitive_ratio` ได้จริง** — ขั้นอื่นวัดได้แค่ relative

#### 8.6.2 L2 — Standard memory benchmark

- **LoCoMo** และ **LongMemEval** สร้างมาเพื่อวัดความจำระยะยาวโดยเฉพาะ มี QA pair + ground truth
- นี่คือที่ที่เทียบกับ MemGPT/Letta, Mem0, Zep ได้ตรง ๆ ด้วยตัวเลขที่วงการยอมรับ
- **มีน้ำหนักตอนเขียน paper มากกว่า demo สวย ๆ** — ให้ priority สูงกว่าที่คิด
- ⚠️ ตรวจสอบ benchmark ใหม่ ณ เวลาที่ทำ วงการนี้ขยับเร็ว

#### 8.6.3 L3 — Replay corpus จากข้อมูลสาธารณะ

แก้ข้อกล่าวหา *"โลกจำลองมันง่ายเกินไป"* โดยตรง

- เอา thread จริงจาก public corpus มาเป็น **event stream ของโลก** แล้วให้ agent สังเคราะห์เข้าไปตอบสนอง
- โครงสร้างการสนทนาเป็นของจริง (จังหวะ ความยาว การแตก thread ความไม่ต่อเนื่อง ช่องว่างเวลา) แต่ ground truth ยังเป็นของเรา
- แหล่งที่ใช้ได้: Reddit academic dump, Stack Exchange data dump, Hacker News API, Wikipedia talk pages
- **หลักการ:** ความยากที่แท้จริงมาจาก *distribution ของบทสนทนาจริง* ไม่ได้มาจากการที่มันอยู่บน facebook.com

#### 8.6.4 L4 — Mastodon harness ★

**นี่คือคำตอบของ "ต้องแตะ API จริงถึงจะรู้ว่าใช้ได้"**

รัน Mastodon instance ของตัวเอง → agent คุยกันผ่าน **ActivityPub / Mastodon REST API จริง**

| ได้อะไร | รายละเอียด |
|---|---|
| Real HTTP surface | auth, token, pagination, error, retry |
| Real rate limiting | บีบให้ scheduler ต้องจัดคิวจริง |
| Real timeline semantics | home/local/federated, boost, reply chain |
| Real threading model | `in_reply_to_id`, conversation tree |
| Real media + notification | mention, follow, DM |
| **Ground truth 100%** | เราคือ admin ของ instance เห็นทุก event |
| **ไม่มีประเด็น ToS** | instance ของเราเอง ไม่มีคนจริงเกี่ยวข้อง |

**สเปกขั้นต่ำ:**

```
Deploy   : docker-compose (mastodon + postgres + redis) บน VPS ตัวเดียว
Mode     : LOCAL_ONLY — ปิด federation (AUTHORIZED_FETCH / ไม่ประกาศตัวออก public)
           registration = closed, provision บัญชีผ่าน CLI/admin API เท่านั้น
Identity : 1 agent = 1 account = 1 access token (scope: read write follow)
Driver   : soma tick loop → เรียก REST API → poll/stream timeline กลับเข้า perceive()
Truth    : mirror ทุก action ลง soma event log ควบคู่ไปด้วย
           → ground truth = event log ของเรา, Mastodon = สนามทดสอบ integration
Bench    : วัด tokens_per_agent_day, llm_call_ratio ที่ N = 10 → 50 → 200
```

**สิ่งที่ L4 พิสูจน์ซึ่ง L1–L3 พิสูจน์ไม่ได้:**
- tick loop ทนต่อ I/O latency + rate limit จริงไหม (§4.3 กฎข้อ 6)
- fallback ladder ทำงานจริงตอน API ล่ม (GATE degradation)
- ระบบยัง deterministic ไหมเมื่อมี external system ที่ควบคุมไม่ได้
- scheduling tier (FOCUS/AMBIENT/DORMANT) คุมต้นทุนได้จริงที่ N สูง

> **ห้ามเปิด federation** ไปยัง instance สาธารณะ — ทันทีที่ federate คือการปล่อย agent ออกสู่เครือข่ายที่มีคนจริง ซึ่งข้ามเส้นเดียวกับ §8.5

#### 8.6.5 L5 — Human evaluation

- ถ้าคำถามคือ *"มันเนียนไหม"* → ให้คนตัดสิน ไม่ใช่ปล่อยลง platform
- ให้ผู้ประเมินแยกระหว่างบทสนทนาที่ agent สร้าง กับ thread จริงจาก L3 corpus
- รายงานเป็น detection rate + inter-rater agreement
- วัด persona consistency ด้วยคนควบคู่กับ metric อัตโนมัติ

#### 8.6.6 ทำไม live public platform ไม่อยู่ใน roadmap

**เหตุผลเชิงวิธีวิจัย (สำคัญกว่า):**
บน platform จริงไม่มี ground truth — ไม่รู้ว่าคู่สนทนาคิดอะไร ไม่รู้ว่า agent *ควร* จำอะไร และคำนวณ OPT ไม่ได้เลยเพราะ OPT ต้องการ trace ที่จบแล้ว **มันพรากตัวชี้ขาดทั้งสามตัวไป**

**เหตุผลเชิงการเข้าถึง:**
- Meta Content Library เป็น **read-only archive ในกล่องปิด** ไม่ใช่ช่องทางให้ agent โต้ตอบ → ต่อให้ได้สิทธิ์ก็ไม่ตอบโจทย์นี้
- ต้องมีสังกัดสถาบันการศึกษา/องค์กรไม่แสวงหากำไร ผ่านการพิจารณาโดย CASD/SOMAR และ API ต้องมี RDUA ลงนามโดยสถาบัน (ระดับ ป.โท อาจต้องมี PhD เป็น PI)
- รันบน Secure Research Environment พร้อม retrieval cap
- *ทางนี้ยังมีประโยชน์สำหรับ **วิเคราะห์ข้อมูล** เพื่อ calibrate persona/topic distribution — แต่ไม่ใช่สำหรับ deployment*

**เหตุผลเชิง ToS:**
สร้างบัญชีอัตโนมัติไปโพสต์บน platform จริงคือการละเมิดเงื่อนไขตรง ๆ ไม่ใช่พื้นที่สีเทา

---

## 9. Roadmap

| Phase | ผลลัพธ์ | Gate ที่ต้องผ่าน |
|---|---|---|
| Phase | ผลลัพธ์ | Gate ที่ต้องผ่าน | Ladder |
|---|---|---|---|
| **0** | Retention engine + working set allocator + OPT harness (synthetic, no LLM) | `S` ชนะ `B2`, `competitive_ratio ≥ 0.7` | L1 |
| **0.5** | รัน policy บน LoCoMo / LongMemEval | เทียบ MemGPT/Mem0/Zep ได้ด้วยตัวเลขมาตรฐาน | L2 |
| **1** | Kernel ขั้นต่ำ: event log + tick + seeded RNG + replay | replay ให้ผลลัพธ์เหมือนเดิม bit-for-bit | — |
| **2** | Cortex: perceive → belief → candidate → score → decide | causal gate test ผ่าน (ดู §10) | — |
| **3** | `soma-pack-social` 10 agents, LLM in loop (in-process) | คุณภาพ ≥ B3 ที่ต้นทุน ≤ 40% | L3 |
| **4** | สเกล 10 → 50 → 200 agents บน Mastodon harness | `tokens_per_agent_day` โตต่ำกว่าเชิงเส้นชัดเจน + GATE degradation ผ่านกับ API จริง | L4 |
| **5** | Consolidation cycle + prospective memory | agent ทำตามเจตนาที่ประกาศไว้ ≥ 80% | L5 |
| **6** | Pack ที่สอง (thesis HR interview) เพื่อพิสูจน์ generality | kernel ไม่ต้องแก้เพื่อรองรับ pack ใหม่ | — |

**Pack ที่สองควรเป็น thesis (AI Agent สัมภาษณ์งาน)** เพราะมีทุกองค์ประกอบที่ SomaOS อ้างว่าแก้ได้: belief เกี่ยวกับผู้สมัครที่ไม่ตรงความจริง, การ revise จากคำตอบใหม่, การเลือกคำถามถัดไปเป็น scored candidate, และความจำเป็นต้องอธิบายได้ว่าทำไมถึงถามคำถามนั้น — ซึ่ง HR ต้องการจริงและ black-box LLM ให้ไม่ได้

---

## 10. Conformance Gates (CI-runnable)

ทำสิ่งที่ prototype ต้นทางทำ manual ให้เป็น spec ที่รันใน CI ได้

```
GATE belief_causality:
  GIVEN world W, agent A, tick T
  PERTURB belief b เพียงตัวเดียว (ถือตัวแปรอื่นคงที่)
  ASSERT decision(T+1) != decision_baseline(T+1)
  ASSERT trace(decision).attributes CONTAINS b

GATE memory_causality:
  GIVEN decision D
  ASSERT soma.explain(D) คืน memory item ที่ระบุได้
  ASSERT ถ้าลบ memory นั้นออก decision เปลี่ยน

GATE replay_determinism:
  GIVEN session S (มี LLM call)
  ASSERT replay(S) ให้ event log เหมือนเดิม bit-for-bit

GATE degradation:
  GIVEN model bus ล่มทั้งหมด
  ASSERT โลกยังเดินต่อได้ด้วย symbolic reflex
  ASSERT ไม่มี state corruption

GATE no_thrash:
  GIVEN budget ≥ working_set(task)
  ASSERT context_churn_rate ต่ำกว่าเกณฑ์
```

**Gate suite นี้คือ differentiator ที่แท้จริง** — ไม่มี agent framework เจ้าไหนทดสอบ causal correctness ของพฤติกรรม

---

## 11. Repo layout ที่เสนอ

```
somaos/
├── kernel/           # L0: event log, tick, txn, rng, snapshot
├── registry/         # L1: entity/component, schema versioning
├── cortex/           # L2: perceive→belief→candidate→score→decide
├── broker/       ★   # L3: retention, working set, budget, consolidation
│   ├── policies/     #     B0..B4 + S — ต้องสลับได้ผ่าน config
│   └── opt/          #     oracle harness
├── modelbus/         # L4: HAL, contracts, fallback ladder, VCR record/replay
├── trace/            # L5: lineage, replay, explain API
├── packs/
│   ├── social/       # pack แรก
│   └── hr/           # pack ที่สอง (thesis)
├── gates/            # conformance suite
└── bench/            # metrics, baselines, report generator
```

**Phase 0 แตะแค่ `broker/` และ `bench/`** ที่เหลือยังไม่ต้องมี

---

## 12. Non-goals และ scope guard

- ❌ ห้ามสร้าง general framework ก่อนมี pack ที่ใช้งานจริงอย่างน้อย 1 ตัว
- ❌ ห้ามให้ LLM เขียน state โดยตรง ไม่ว่ากรณีใด
- ❌ ห้ามทำ UI/frontend ก่อน Phase 3
- ❌ ห้าม optimize ก่อนมีตัวเลขจาก bench
- ❌ ห้ามเพิ่ม pack ที่สามก่อน pack ที่สองพิสูจน์ generality
- ⚠️ คำว่า "OS" เป็นข้อจำกัดการออกแบบ ไม่ใช่การตลาด — ถ้าอันไหนไม่ตรง metaphor OS ให้ตั้งคำถามกับมัน

---

## 13. คำถามเปิดที่ยังไม่มีคำตอบ

1. **Belief revision semantics** — จะใช้ AGM postulates, Bayesian update, หรือ non-monotonic + defeater? ต้องเลือกและ commit ไม่งั้น "belief revision" จะกลายเป็นแค่ `dict[key] = value`
2. **นิยาม `τ` ของ working set** — คงที่ต่อ pack, หรือ adaptive ตาม task?
3. **Surprise สำหรับ observation ที่ไม่มี belief ทำนายไว้เลย** — นับเป็น surprise สูงสุด หรือเป็นหมวดแยก?
4. **Consolidation ควรรันบ่อยแค่ไหน** — ทุก N ticks, หรือ trigger เมื่อ episodic buffer เต็ม?
5. **Multi-agent shared memory** — agent สองตัวที่อยู่เหตุการณ์เดียวกัน แชร์ episodic record หรือมีสำเนาของตัวเอง? (มีผลกับต้นทุนมหาศาล)
6. **Schema migration ของ belief** ตอนโลกรันมาแล้วเป็นเดือน — upcasting on read พอไหม?

---

## 14. Assets ที่มีอยู่แล้วและควรรียูส

จากงานเดิม (SynaptaOS) มีของที่ย้ายมาใช้ได้ตรง ๆ:

| ของเดิม | ใช้ที่ไหนใน SomaOS |
|---|---|
| Selective Schema Injection (two-layer registry) | ต้นแบบของ Context Broker เวอร์ชัน single-domain |
| GraphRAG / knowledge graph | Semantic memory tier |
| Three-tier pre-execution fallback | ยกขึ้นเป็น fallback ladder ระดับ kernel |
| Correlation ID / Actor model + QoS 2 exactly-once | async decision + idempotent effect application |
| Static-before-dynamic prompt zoning | บังคับเป็นกฎของ ContextBundle layout |
| Deterministic evaluator แทน LLM evaluator | หลักการเดียวกับ policy-driven memory |
| Langfuse | observability ระดับ LLM call — แต่ **state lineage ต้องสร้างเอง** |

---

## 15. คำสั่งสำหรับ Claude Code

เมื่อทำงานในโปรเจกต์นี้:

1. อ่านไฟล์นี้ก่อนเสมอ ถ้าคำขอขัดกับ §12 (non-goals) ให้ทักท้วงก่อนทำ
2. **งานปัจจุบันคือ Phase 0 เท่านั้น** — อย่าเผลอสร้าง kernel, cortex, หรือ pack
3. ทุก policy ต้อง implement ตาม interface เดียวกัน สลับผ่าน config ได้ เพื่อ benchmark เทียบกันตรง ๆ
4. เขียน test ก่อนเสมอสำหรับ retention scoring — มันคือ pure function ควรมี coverage สูง
5. อย่าเพิ่ม dependency ที่ไม่จำเป็น Phase 0 ควรรันได้ด้วย stdlib + numpy เป็นหลัก
6. ทุก metric ต้อง export เป็น structured data ไม่ใช่ print
7. ถ้าเจอว่าสมมติฐานใน §7.4 (kill criteria) เป็นจริง — **บอกตรง ๆ อย่าหาทางแก้ตัวเลข**
