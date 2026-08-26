"""Scope-guard tests. See plans/wp/WP-10-determinism-ci.md #3 and
CLAUDE.md's PHASE 0 scope rule -- these exist so scope creep into
kernel/registry/cortex/modelbus/trace/packs shows up as a failing test,
not something someone notices during review."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOMAOS = ROOT / "somaos"

FORBIDDEN_DIRS = ["kernel", "registry", "cortex", "modelbus", "trace", "packs"]


@pytest.mark.parametrize("name", FORBIDDEN_DIRS)
def test_forbidden_directory_does_not_exist(name):
    assert not (SOMAOS / name).exists(), (
        f"somaos/{name}/ exists but Phase 0 is scoped to broker/ + bench/ only "
        "(CLAUDE.md, target_SomaOS.md #12/#15)"
    )


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mods.append(alias.name)
    return mods


def test_bench_trace_does_not_import_policies():
    for path in (SOMAOS / "bench" / "trace").glob("*.py"):
        for mod in _imported_modules(path):
            assert "broker.policies" not in mod, f"{path} imports {mod} (WP-02 #5.3)"


def test_report_does_not_import_broker():
    path = SOMAOS / "bench" / "report.py"
    for mod in _imported_modules(path):
        assert "broker" not in mod, f"report.py imports {mod} (WP-09 acceptance)"


def test_retention_only_imports_stdlib_and_types():
    path = SOMAOS / "broker" / "retention.py"
    allowed_prefixes = ("somaos.broker.types", "__future__", "dataclasses", "math", "typing")
    for mod in _imported_modules(path):
        assert mod.startswith(allowed_prefixes), (
            f"retention.py imports {mod}; must stay pure (only stdlib + "
            "somaos.broker.types), see plans/02_INTERFACES.md #3"
        )


def test_no_third_party_dependency_besides_numpy():
    import tomllib

    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    for dep in data["project"]["dependencies"]:
        name = dep.split(">=")[0].split("==")[0].split("[")[0].strip()
        assert name == "numpy", f"unexpected Phase 0 dependency: {dep}"


STDLIB_ISH = {
    "__future__", "dataclasses", "typing", "enum", "math", "random", "hashlib",
    "json", "itertools", "bisect", "collections", "argparse", "glob", "pathlib",
    "statistics", "time", "sys", "os", "ast", "concurrent", "concurrent.futures",
    "tomllib", "copy", "subprocess",
}


def test_no_third_party_import_anywhere_in_somaos():
    for path in SOMAOS.rglob("*.py"):
        for mod in _imported_modules(path):
            top = mod.split(".")[0]
            if top == "somaos":
                continue
            assert top in STDLIB_ISH or top == "numpy", (
                f"{path} imports {mod!r} -- not stdlib and not numpy "
                "(CLAUDE.md: stdlib + numpy only, ask before adding anything else)"
            )
