# WP-00 — Repo scaffold & conventions

**Depends on:** —  **Blocks:** ทุกอย่าง  **Est:** 0.5 d

## Goal
โครงโปรเจกต์ที่รัน `pytest` และ `python -m somaos.bench.runner` ได้ โดยยังไม่มี logic

## Files
```
pyproject.toml            # setuptools, python >= 3.11, deps: numpy เท่านั้น
.gitignore                # runs/, __pycache__, .pytest_cache, *.egg-info
somaos/__init__.py        # __version__ = "0.0.0-phase0"
somaos/broker/__init__.py
somaos/broker/policies/__init__.py
somaos/broker/opt/__init__.py
somaos/bench/__init__.py
somaos/bench/trace/__init__.py
somaos/bench/configs/phase0.json      # stub ตาม WP-08
somaos/util/rng.py        # named-stream seeding (D-08)
somaos/util/hashing.py    # canonical_json() + sha256_str()
tests/conftest.py
```

## Public API ที่ต้องมี
```python
# somaos/util/rng.py
def stream_seed(seed_root: str, stream_name: str) -> int
def make_rng(seed_root: str, stream_name: str) -> random.Random

# somaos/util/hashing.py
def canonical_json(obj) -> str      # sort_keys, separators=(",",":"), ensure_ascii=False
def sha256_str(s: str) -> str       # คืน "sha256:<hex>"
```

## Acceptance
- `pytest -q` ผ่าน (มี test อย่างน้อย: `make_rng` ให้ลำดับเดิมข้าม process — รันด้วย `PYTHONHASHSEED=0` และ `=1` แล้วผลเท่ากัน)
- `canonical_json({"b":1,"a":2}) == '{"a":2,"b":1}'`
- `python -c "import somaos; print(somaos.__version__)"` ทำงาน
- ไม่มี dependency นอก numpy + stdlib (ตรวจด้วย test ที่อ่าน pyproject)

## DoD
- README สั้น ๆ ชี้ไป `CLAUDE.md`
- ไม่มีไฟล์นอก `somaos/`, `tests/`, root config

## Prompt สำหรับ Sonnet
> อ่าน `CLAUDE.md`, `plans/00_PHASE0_MASTER_PLAN.md`, `plans/01_DECISIONS.md` (โดยเฉพาะ D-08) แล้วทำ `plans/wp/WP-00-scaffold.md` ให้จบ
> สร้างเฉพาะไฟล์ที่ระบุ ห้ามเพิ่ม dependency ห้ามเขียน logic ของ WP อื่น
> จบด้วยการรัน `pytest -q` แล้วรายงานผลตามจริง
