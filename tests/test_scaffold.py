import json
import subprocess
import sys

from somaos.util.hashing import canonical_json, sha256_str
from somaos.util.rng import make_rng, stream_seed


def test_canonical_json_sorts_keys():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_no_extra_whitespace():
    assert canonical_json([1, 2, 3]) == "[1,2,3]"


def test_sha256_str_format():
    h = sha256_str("hello")
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_sha256_str_deterministic():
    assert sha256_str("x") == sha256_str("x")


def test_stream_seed_deterministic():
    assert stream_seed("root", "obs") == stream_seed("root", "obs")


def test_stream_seed_differs_by_stream():
    assert stream_seed("root", "obs") != stream_seed("root", "query")


def test_stream_seed_differs_by_root():
    assert stream_seed("a", "obs") != stream_seed("b", "obs")


def test_make_rng_reproducible_sequence():
    r1 = make_rng("root", "obs")
    r2 = make_rng("root", "obs")
    seq1 = [r1.random() for _ in range(20)]
    seq2 = [r2.random() for _ in range(20)]
    assert seq1 == seq2


def test_make_rng_stable_across_hashseed():
    """stream_seed must not depend on PYTHONHASHSEED (it uses sha256, not hash())."""
    code = (
        "from somaos.util.rng import stream_seed; "
        "print(stream_seed('root','obs'))"
    )
    import os

    env0 = dict(**__import__("os").environ, PYTHONHASHSEED="0")
    env1 = dict(**__import__("os").environ, PYTHONHASHSEED="1")
    out0 = subprocess.run([sys.executable, "-c", code], env=env0, capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    out1 = subprocess.run([sys.executable, "-c", code], env=env1, capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    assert out0.stdout.strip() == out1.stdout.strip(), (out0.stderr, out1.stderr)


def test_version_importable():
    import somaos

    assert somaos.__version__


def test_only_numpy_dependency():
    import tomllib

    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    deps = data["project"]["dependencies"]
    for d in deps:
        name = d.split(">=")[0].split("==")[0].split("[")[0].strip()
        assert name in {"numpy"}, f"unexpected dependency: {d}"
