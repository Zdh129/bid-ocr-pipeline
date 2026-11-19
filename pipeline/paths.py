"""仓库根目录与数据路径。导入时不联网、不读取密钥值。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_BAIDU_ROOT = Path("/workspace/pdf-test/baidu")
LAB_PARENT = Path("/workspace/pdf-test")


def alias_baidu_keys() -> None:
    """若只设置了 BAIDU_API_KEY / BAIDU_SECRET_KEY，则映射到冻结代码使用的变量名。不打印值。"""
    if not os.environ.get("BAIDU_OCR_API_KEY") and os.environ.get("BAIDU_API_KEY"):
        os.environ["BAIDU_OCR_API_KEY"] = os.environ["BAIDU_API_KEY"]
    if not os.environ.get("BAIDU_OCR_SECRET_KEY") and os.environ.get("BAIDU_SECRET_KEY"):
        os.environ["BAIDU_OCR_SECRET_KEY"] = os.environ["BAIDU_SECRET_KEY"]


def prepare_sys_path(*extra: Path) -> None:
    parts = [
        REPO_ROOT / "src",
        REPO_ROOT / "scripts",
        REPO_ROOT / "rules" / "baseline_c",
        REPO_ROOT / "pipeline",
        *extra,
    ]
    for p in reversed(parts):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def dataset_dir(name: str) -> Path:
    """data/<集名>/。兼容只给出目录路径的情况。"""
    p = Path(name)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    return (REPO_ROOT / "data" / name).resolve()


def remap_lab_path(path: str | Path) -> Path:
    """把原试验机上的绝对路径映射到本仓库。仅用于加载冻结脚本时的运行时替换。"""
    p = Path(path)
    try:
        rel = p.relative_to(LAB_BAIDU_ROOT)
        mapped = REPO_ROOT / rel
        # 原 output/盲测_版面约束/冻结规则 等目录已改放到 rules/
        mapping = {
            Path("output/盲测_版面约束/冻结规则"): REPO_ROOT / "rules" / "baseline_c",
            Path("output/盲测_版面约束/版面分析优化计划/试算/冻结_新盲测前"): REPO_ROOT / "rules" / "round1",
            Path("output/盲测_版面约束/版面分析优化计划/试算/第2轮/冻结"): REPO_ROOT / "rules" / "round2",
            Path("output/synth18e_prep/frozen_方案E.json"): REPO_ROOT / "output" / "synth18e_prep" / "frozen_方案E.json",
        }
        for old, new in mapping.items():
            if rel == old or str(rel).startswith(str(old) + "/"):
                tail = rel.relative_to(old) if rel != old else Path(".")
                return (new / tail).resolve() if tail != Path(".") else new.resolve()
        return mapped
    except ValueError:
        pass
    try:
        rel = p.relative_to(LAB_PARENT)
        # /workspace/pdf-test/synth18f 等数据集 → data/synth18f
        return (REPO_ROOT / "data" / rel).resolve()
    except ValueError:
        return p
